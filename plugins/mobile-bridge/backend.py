"""Third-party LAN transport using the public PluginHost chat API."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import queue
from pathlib import Path
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from nexus.task import TaskConflict, TaskNotFound, TaskValidationError

MAX_BODY = 256 * 1024


class Bridge:
    def __init__(self, host):
        self.host = host
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.subscriptions = set()
        self.clients = set()
        self.code = ''
        self.code_until = 0
        self.code_attempts = 0
        self.file = Path(host.data_dir) / 'mobile-devices.json'
        self.devices = json.loads(self.file.read_text('utf-8')) if self.file.exists() else {}
        self.server = None
        self.thread = None

    def start(self):
        if getattr(self.host, 'chat_api_version', None) != 1:
            raise RuntimeError('Nexus chat API version 1 is required')
        settings = self.host.settings
        bind, port = self.validate_settings(settings)
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def setup(self):
                super().setup()
                self.connection.settimeout(20)
                with bridge.lock:
                    bridge.clients.add(self.connection)

            def finish(self):
                try:
                    super().finish()
                finally:
                    with bridge.lock:
                        bridge.clients.discard(self.connection)

            def log_message(self, *args):
                pass  # Pair codes, request bodies and bearer credentials never enter logs.

            def reply(self, status, payload):
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                self.wfile.write(data)

            def body(self):
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('不支持分块请求体')
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    raise ValueError('请求必须为 application/json')
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > MAX_BODY:
                    raise ValueError('请求为空或超过 256 KiB')
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError('请求未完整收到')
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError('请求必须为 JSON 对象')
                return value

            def authorized(self):
                raw = self.headers.get('Authorization', '')
                if not raw.startswith('Bearer '):
                    return False
                digest = hashlib.sha256(raw[7:].encode()).hexdigest()
                with bridge.lock:
                    return digest in bridge.devices

            def dispatch(self, method):
                try:
                    if self.headers.get('Origin') is not None:
                        return self.reply(403, {'error': '仅接受配对 APP 连接，不允许网页跨域访问'})
                    if bridge.closed.is_set():
                        return self.reply(503, {'error': '插件已停止'})
                    if method == 'POST' and self.path == '/pair':
                        value = self.body()
                        token = bridge.pair(value)
                        return self.reply(200, {'token': token})
                    if not self.authorized():
                        return self.reply(401, {'error': '设备未配对或已解除配对'})
                    if method == 'GET' and self.path == '/chat':
                        return self.reply(200, bridge.host.chat_snapshot())
                    if method == 'POST' and self.path == '/messages':
                        value = self.body()
                        text, request_id = value.get('text'), value.get('request_id')
                        if not isinstance(text, str) or not text.strip():
                            raise ValueError('消息不能为空')
                        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
                            raise ValueError('request_id 为必填字符串，最多 200 字符')
                        return self.reply(202, bridge.host.chat_send(text, request_id))
                    if method == 'GET' and self.path == '/events':
                        return self.events()
                    return self.reply(404, {'error': '接口不存在'})
                except TaskNotFound as error:
                    self.reply(404, {'error': str(error)})
                except TaskConflict as error:
                    self.reply(409, {'error': str(error)})
                except TaskValidationError as error:
                    self.reply(400, {'error': str(error)})
                except PermissionError as error:
                    self.reply(403, {'error': str(error)})
                except (ValueError, TypeError) as error:
                    self.reply(400, {'error': str(error)})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                    pass
                except OSError as error:
                    if not bridge.closed.is_set():
                        self.reply(502, {'error': 'Nexus 连接失败：' + str(error)})

            def events(self):
                subscription = bridge.host.chat_subscribe()
                with bridge.lock:
                    if bridge.closed.is_set() or not self.authorized():
                        subscription.close()
                        return
                    bridge.subscriptions.add(subscription)
                try:
                    snapshot = bridge.host.chat_snapshot()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.close_connection = True
                    self.wfile.write(('event: snapshot\ndata: ' + json.dumps(snapshot, ensure_ascii=False) + '\n\n').encode())
                    self.wfile.flush()
                    while not bridge.closed.is_set() and self.authorized():
                        try:
                            event = subscription.get(timeout=1)
                        except queue.Empty:
                            self.wfile.write(b': heartbeat\n\n')
                        else:
                            if event is None:
                                break
                            self.wfile.write(('event: runtime\ndata: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode())
                        self.wfile.flush()
                finally:
                    with bridge.lock:
                        bridge.subscriptions.discard(subscription)
                    subscription.close()

            def do_GET(self):
                self.dispatch('GET')

            def do_POST(self):
                self.dispatch('POST')

        self.server = ThreadingHTTPServer((bind, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True, name='nexus-mobile-http')
        self.thread.start()

    @staticmethod
    def validate_settings(settings):
        bind = str(settings.get('bind') or '0.0.0.0')
        ip = ipaddress.ip_address(bind)
        if ip.version != 4:
            raise ValueError('第一版使用 IPv4 局域网地址')
        port = settings.get('port', 18767)
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('端口必须为 0–65535 的整数')
        return bind, port

    def persist(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.file.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.devices, ensure_ascii=False), encoding='utf-8')
        os.chmod(temporary, 0o600)
        temporary.replace(self.file)

    def pair(self, value):
        with self.lock:
            candidate = value.get('code')
            if not isinstance(candidate, str) or not self.code or time.time() >= self.code_until or self.code_attempts >= 10:
                raise PermissionError('配对码不存在、已使用或已失效，请在电脑端重新生成')
            if not hmac.compare_digest(candidate.strip().upper(), self.code):
                self.code_attempts += 1
                raise PermissionError('配对码错误')
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            previous = dict(self.devices)
            self.devices[digest] = {'name': str(value.get('device_name') or 'Android')[:80], 'paired_at': int(time.time())}
            try:
                self.persist()
            except OSError:
                self.devices = previous
                raise
            self.code = ''
            self.code_until = 0
            return token

    def snapshot(self):
        addresses = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                addr = info[4][0]
                if not addr.startswith('127.'):
                    addresses.add(addr)
        except OSError:
            pass
        port = self.server.server_address[1] if self.server else None
        with self.lock:
            return {'running': bool(self.server and not self.closed.is_set()), 'bind': self.server.server_address[0] if self.server else '', 'port': port,
                'addresses': [f'http://{addr}:{port}' for addr in sorted(addresses)],
                'pair_code': self.code if time.time() < self.code_until else '', 'pair_expires_at': int(self.code_until),
                'devices': list(self.devices.values()), 'settings': self.host.settings}

    @staticmethod
    def interrupt(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def drop_clients(self):
        with self.lock:
            sockets = list(self.clients)
            subscriptions = list(self.subscriptions)
        for subscription in subscriptions:
            subscription.close()
        for sock in sockets:
            self.interrupt(sock)

    def call(self, action, values):
        if action == 'snapshot':
            return self.snapshot()
        if action == 'pair':
            with self.lock:
                self.code = secrets.token_hex(4).upper()
                self.code_until = time.time() + 300
                self.code_attempts = 0
            return self.snapshot()
        if action == 'revoke':
            with self.lock:
                previous = self.devices
                self.devices = {}
                try:
                    self.persist()
                except OSError:
                    self.devices = previous
                    raise
                self.code = ''
                self.code_until = 0
            self.drop_clients()
            return self.snapshot()
        if action == 'configure':
            bind, port = self.validate_settings(values)
            self.host.save_settings({'bind': bind, 'port': port})
            return {'restart_required': True}
        raise ValueError('未知手机接入操作')

    def close(self):
        self.closed.set()
        self.drop_clients()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)


def create_plugin(host):
    return Bridge(host)

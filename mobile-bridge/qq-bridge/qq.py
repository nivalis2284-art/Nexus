"""QQ's HTTP API and WSS transport; no host configuration is changed."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import quote, urlsplit

import httpx


class QQClient:
    def __init__(self, app_id, secret):
        self.app_id, self.secret = app_id, secret
        self.token, self.expires = '', 0
        self.lock = threading.Lock()

    def access_token(self):
        with self.lock:
            if time.monotonic() >= self.expires:
                with httpx.Client(timeout=20) as client:
                    response = client.post('https://bots.qq.com/app/getAppAccessToken',
                                           json={'appId': self.app_id, 'clientSecret': self.secret})
                    if response.is_error:
                        raise ValueError(f'QQ 凭据获取失败：HTTP {response.status_code}')
                    body = response.json()
                self.token = body['access_token']
                self.expires = time.monotonic() + max(1, int(body['expires_in']) - 60)
            return self.token

    def request(self, method, path, **kwargs):
        with httpx.Client(timeout=30) as client:
            response = client.request(method, 'https://api.sgroup.qq.com' + path,
                headers={'Authorization': 'QQBot ' + self.access_token(),
                         'X-Union-Appid': self.app_id}, **kwargs)
        if response.is_error:
            try:
                code = response.json().get('code', '')
            except ValueError:
                code = ''
            raise ValueError(f'QQ API 失败：HTTP {response.status_code}，错误码 {code}')
        return response.json()

    def gateway(self):
        return self.request('GET', '/gateway')['url']

    @staticmethod
    def conversation(kind, target):
        if kind not in ('c2c', 'group') or not isinstance(target, str) or not target:
            raise ValueError('须指定 c2c/group 和目标 OpenID')
        return '/v2/' + ('users' if kind == 'c2c' else 'groups') + '/' + quote(target, safe='')

    def send(self, kind, target, content, reply_id='', sequence=1, image=None):
        path = self.conversation(kind, target)
        payload = {'msg_type': 0, 'content': content, 'msg_seq': sequence}
        if reply_id:
            payload['msg_id'] = reply_id
        if image:
            file_info = self.request('POST', path + '/files', json={
                'file_type': 1, 'file_name': image['name'],
                'file_data': image['data'], 'srv_send_msg': False})['file_info']
            payload.update(msg_type=7, media={'file_info': file_info})
        return self.request('POST', path + '/messages', json=payload)


class WebSocket:
    """RFC 6455 framing adapted from the existing browser plugin, with TLS."""
    def __init__(self, url):
        parts = urlsplit(url)
        if parts.scheme != 'wss' or not parts.hostname or not parts.hostname.endswith('.qq.com'):
            raise ValueError('QQ 网关必须是 qq.com 的 WSS 地址')
        raw = socket.create_connection((parts.hostname, parts.port or 443), timeout=20)
        try:
            self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname)
            key = base64.b64encode(os.urandom(16)).decode()
            path = parts.path or '/'
            if parts.query:
                path += '?' + parts.query
            self.sock.sendall((f'GET {path} HTTP/1.1\r\nHost: {parts.netloc}\r\n'
                f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n'
                'Sec-WebSocket-Version: 13\r\n\r\n').encode())
            response = b''
            while not response.endswith(b'\r\n\r\n'):
                chunk = self.sock.recv(1)
                if not chunk or len(response) > 16384:
                    raise ValueError('QQ WebSocket 握手不完整')
                response += chunk
            headers = {}
            for line in response.decode('latin1').split('\r\n')[1:]:
                if ':' in line:
                    name, value = line.split(':', 1)
                    headers[name.lower()] = value.strip()
            expected = base64.b64encode(hashlib.sha1(
                (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            if response.split(b'\r\n')[0].split()[1] != b'101' or headers.get('sec-websocket-accept') != expected:
                raise ValueError('QQ WebSocket 握手校验失败')
        except BaseException:
            raw.close()
            if hasattr(self, 'sock'):
                self.sock.close()
            raise
        self.send_lock = threading.Lock()

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def send(self, value):
        self._send(json.dumps(value, ensure_ascii=False).encode())

    def _send(self, data, opcode=1):
        size, mask = len(data), os.urandom(4)
        header = bytes([128 | opcode])
        if size < 126:
            header += bytes([128 | size])
        elif size < 65536:
            header += bytes([254]) + struct.pack('!H', size)
        else:
            header += bytes([255]) + struct.pack('!Q', size)
        with self.send_lock:
            self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, size):
        chunks = bytearray()
        while len(chunks) < size:
            part = self.sock.recv(size - len(chunks))
            if not part:
                raise ConnectionError('QQ WebSocket 已断开')
            chunks.extend(part)
        return bytes(chunks)

    def receive(self):
        chunks = []
        while True:
            first, second = self._read(2)
            size = second & 127
            if size == 126:
                size = struct.unpack('!H', self._read(2))[0]
            elif size == 127:
                size = struct.unpack('!Q', self._read(8))[0]
            if second & 128:
                raise ValueError('QQ 服务端帧不应带掩码')
            data = self._read(size)
            opcode = first & 15
            if opcode == 8:
                code = struct.unpack('!H', data[:2])[0] if len(data) >= 2 else 1000
                raise ConnectionError(f'QQ WebSocket 关闭：{code}')
            if opcode == 9:
                self._send(data, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1):
                raise ValueError('QQ 网关发送了非文本消息')
            chunks.append(data)
            if first & 128:
                return json.loads(b''.join(chunks))

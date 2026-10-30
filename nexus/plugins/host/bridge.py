"""Language-neutral loopback bridge, owned by plugin runtime rather than Web UI."""
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Bridge:
    def __init__(self, runtime):
        self.runtime = runtime
        self.tokens = {}
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                owner = next((p for p, t in list(bridge.tokens.items()) if hmac.compare_digest(t, token)), None)
                if not owner:
                    self.send_error(401); return
                try:
                    if self.path != '/v1/call': raise ValueError('未知桥接接口')
                    body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                    result = bridge.runtime.invoke(owner, body['method'], body.get('params') or {})
                    data, code = {'result': result}, 200
                except Exception as error:
                    data, code = {'error': {'code': type(error).__name__, 'message': str(error)}}, 400
                payload = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(payload))); self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, name='plugin-host-bridge', daemon=True)
        self.thread.start()

    def environment(self, owner):
        token = self.tokens.setdefault(owner, secrets.token_urlsafe(32))
        return {'NEXUS_HOST_URL': f'http://127.0.0.1:{self.server.server_port}/v1/call', 'NEXUS_PLUGIN_TOKEN': token}

    def revoke(self, owner):
        self.tokens.pop(owner, None)

    def close(self):
        self.tokens.clear()
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

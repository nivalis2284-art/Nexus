"""Small synchronous WebSocket client for a local Chrome DevTools endpoint."""
import base64
import hashlib
import json
import os
import socket
import struct
import time
from urllib.parse import urlsplit


class CDP:
    def __init__(self, url):
        parts = urlsplit(url)
        if parts.scheme != 'ws' or parts.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('DevTools endpoint must be a local websocket')
        self.sock = socket.create_connection((parts.hostname, parts.port), timeout=10)
        self.sock.settimeout(10)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parts.path + (('?' + parts.query) if parts.query else '')
        self.sock.sendall((f'GET {path} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        response = b''
        while not response.endswith(b'\r\n\r\n'):
            chunk = self.sock.recv(1)
            if not chunk or len(response) > 16384:
                self.sock.close()
                raise RuntimeError('Invalid DevTools websocket handshake')
            response += chunk
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        if b' 101 ' not in response.split(b'\r\n', 1)[0] or expected.lower().encode() not in response.lower():
            self.sock.close()
            raise RuntimeError('DevTools websocket handshake failed')
        self.seq = 0
        self.deadline = None

    def close(self):
        self.sock.close()

    def _send(self, payload, opcode=1):
        mask = os.urandom(4)
        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack('!H', length)
        else:
            header += bytes([0x80 | 127]) + struct.pack('!Q', length)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _read(self, n):
        result = b''
        while len(result) < n:
            if self.deadline is not None:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError('DevTools command timed out')
                self.sock.settimeout(remaining)
            chunk = self.sock.recv(n - len(result))
            if not chunk:
                raise RuntimeError('DevTools connection closed')
            result += chunk
        return result

    def _message(self):
        chunks = []
        while True:
            first, second = self._read(2)
            length = second & 127
            if length == 126:
                length = struct.unpack('!H', self._read(2))[0]
            elif length == 127:
                length = struct.unpack('!Q', self._read(8))[0]
            mask = self._read(4) if second & 128 else None
            payload = self._read(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            opcode = first & 15
            if opcode == 8:
                raise RuntimeError('DevTools connection closed')
            if opcode == 9:
                self._send(payload, 10)
                continue
            if opcode == 10:
                continue
            chunks.append(payload)
            if first & 128:
                return json.loads(b''.join(chunks))

    def call(self, method, **params):
        deadline = time.monotonic() + 10
        self.deadline = deadline
        self.seq += 1
        ident = self.seq
        self._send(json.dumps({'id': ident, 'method': method, 'params': params}).encode())
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('DevTools command timed out')
            self.sock.settimeout(remaining)
            message = self._message()
            if message.get('id') == ident:
                if 'error' in message:
                    raise RuntimeError(message['error'].get('message', 'DevTools error'))
                return message.get('result', {})

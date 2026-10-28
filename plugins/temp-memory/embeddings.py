"""Optional OpenAI-compatible embedding client; no models are downloaded."""
from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config: dict, key: str = ''):
        self.url = str(config.get('embedding_url') or config.get('baseURL') or '').strip()
        self.model = str(config.get('embedding_model') or '').strip()
        self.key = key or ''

    @property
    def configured(self):
        return bool(self.url and self.model)

    @property
    def model_id(self):
        return hashlib.sha256(json.dumps([self.url, self.model], ensure_ascii=False,
                                         separators=(',', ':')).encode('utf-8')).hexdigest()

    def _endpoint(self):
        try:
            parts = urllib.parse.urlsplit(self.url)
            if (parts.scheme.lower() not in ('http', 'https') or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or '?' in self.url or '#' in self.url):
                raise ValueError
            # Validate invalid ports and malformed bracketed addresses now.
            _ = parts.port
            path = parts.path.rstrip('/')
            if not path.endswith('/embeddings'):
                path += '/embeddings'
            return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, '', '')), parts.hostname.lower()
        except ValueError:
            raise ValueError('Invalid embedding endpoint URL') from None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.configured:
            raise ValueError('Embedding endpoint and model are required')
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise ValueError('Embedding input must be a list of strings')
        endpoint, hostname = self._endpoint()
        if not texts:
            return []
        body = json.dumps({'model': self.model, 'input': texts}, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.key:
            headers['Authorization'] = 'Bearer ' + self.key
        request = urllib.request.Request(endpoint, data=body, headers=headers, method='POST')
        handlers = [_NoRedirect()]
        if hostname in ('localhost', '127.0.0.1'):
            handlers.append(urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(request, timeout=5) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'Embedding provider returned HTTP {exc.code}') from None
        except (OSError, ValueError, UnicodeError):
            raise RuntimeError('Embedding request failed or returned invalid JSON') from None
        try:
            data = payload['data']
            if not isinstance(data, list) or len(data) != len(texts):
                raise ValueError
            result = [None] * len(texts)
            dimension = None
            for item in data:
                index = item['index']
                vector = item['embedding']
                if (isinstance(index, bool) or not isinstance(index, int)
                        or not 0 <= index < len(texts) or result[index] is not None
                        or not isinstance(vector, list) or not vector):
                    raise ValueError
                if dimension is None:
                    dimension = len(vector)
                if len(vector) != dimension:
                    raise ValueError
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                       for v in vector):
                    raise ValueError
                result[index] = [float(v) for v in vector]
            return result
        except (TypeError, KeyError, ValueError, OverflowError):
            raise RuntimeError('Embedding provider returned invalid vectors') from None

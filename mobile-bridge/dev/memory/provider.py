"""Small OpenAI-compatible clients; credentials never appear in returned configuration."""
import json
import math
from urllib.request import Request, urlopen
from urllib.error import HTTPError

class Provider:
    def request(self, base, key, endpoint, payload, timeout):
        url = base.rstrip('/')
        if not url.startswith(('https://', 'http://')):
            raise ValueError('请配置有效的模型 API 地址')
        if not url.endswith('/' + endpoint):
            url += '/' + endpoint
        req = Request(url, json.dumps(payload, ensure_ascii=False).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            raise ValueError(f'模型服务返回 HTTP {error.code}') from None

    def chat(self, settings, secrets, instruction, material):
        result = self.request(settings['llm_url'], secrets.get('llm_key', ''), 'chat/completions', {
            'model': settings['llm_model'], 'messages': [{'role': 'system', 'content': instruction},
            {'role': 'user', 'content': json.dumps(material, ensure_ascii=False)}]}, 90)
        text = result['choices'][0]['message']['content'].strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0]
        return json.loads(text)

    def embed(self, settings, secrets, texts):
        result = self.request(settings['embedding_url'], secrets.get('embedding_key', ''), 'embeddings', {
            'model': settings['embedding_model'], 'input': texts}, 10)
        rows = sorted(result['data'], key=lambda x: x['index'])
        if len(rows) != len(texts) or [r['index'] for r in rows] != list(range(len(texts))):
            raise ValueError('嵌入结果数量或顺序无效')
        vectors = []
        for row in rows:
            vector = row['embedding']
            if not vector or not all(type(x) in (int, float) and math.isfinite(x) for x in vector):
                raise ValueError('嵌入向量无效')
            norm = math.sqrt(sum(x*x for x in vector))
            if not norm:
                raise ValueError('嵌入向量不能为零')
            vectors.append([x/norm for x in vector])
        return vectors

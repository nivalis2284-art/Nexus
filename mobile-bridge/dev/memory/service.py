"""Local stdio MCP service. Only memory_* tools are exposed to the host model."""
import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from engine import Engine

TOOLS = [
    {'name': 'memory_search', 'description': '检索长期记忆粒子。semantic 为语义检索，exact 为词句匹配。先查粒子，必要时再展开原文；保留来源、时间和不确定性。',
     'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}, 'mode': {'type': 'string', 'enum': ['semantic', 'exact'], 'default': 'semantic'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 5}}, 'required': ['query'], 'additionalProperties': False}, 'annotations': {'readOnlyHint': True}},
    {'name': 'memory_get', 'description': '按 ID 读取记忆粒子。', 'inputSchema': {'type': 'object', 'properties': {'particle_id': {'type': 'string'}}, 'required': ['particle_id'], 'additionalProperties': False}, 'annotations': {'readOnlyHint': True}},
    {'name': 'memory_expand', 'description': '粒子信息不足时，按 ID 展开绑定的原文。仅取必要依据；原文将进入主交流上下文。', 'inputSchema': {'type': 'object', 'properties': {'particle_id': {'type': 'string'}}, 'required': ['particle_id'], 'additionalProperties': False}, 'annotations': {'readOnlyHint': True}},
    {'name': 'memory_admin', 'description': 'Private plugin bridge.', 'inputSchema': {'type': 'object', 'properties': {'action': {'type': 'string'}, 'values': {'type': 'object'}}, 'required': ['action']}}
]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True)
    engine = Engine(parser.parse_args().data)
    output_lock = threading.Lock()
    def reply(request):
        key = request.get('id')
        if key is None:
            return
        try:
            method, params = request.get('method'), request.get('params') or {}
            if method == 'initialize':
                result = {'protocolVersion': params.get('protocolVersion', '2025-06-18'), 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'nexus-memory', 'version': '0.1.0'}}
            elif method == 'ping': result = {}
            elif method == 'tools/list': result = {'tools': TOOLS}
            elif method == 'tools/call':
                name, args = params['name'], params.get('arguments') or {}
                if name == 'memory_admin':
                    data = engine.dispatch(args['action'], args.get('values') or {})
                elif name in ('memory_search', 'memory_get', 'memory_expand'):
                    data = engine.dispatch(name, args)
                else:
                    raise ValueError('Unknown tool')
                result = {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'isError': False}
            else:
                raise ValueError('Unsupported method')
            body = {'jsonrpc': '2.0', 'id': key, 'result': result}
        except Exception as error:
            if request.get('method') == 'tools/call':
                body = {'jsonrpc': '2.0', 'id': key, 'result': {'content': [{'type': 'text', 'text': str(error)}], 'isError': True}}
            else:
                body = {'jsonrpc': '2.0', 'id': key, 'error': {'code': -32602, 'message': str(error)}}
        with output_lock:
            print(json.dumps(body, ensure_ascii=False), flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        try:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                except ValueError:
                    continue
                pool.submit(reply, request)
        finally:
            engine.stop.set()
    if engine.worker:
        engine.worker.join(timeout=2)

if __name__ == '__main__':
    main()

"""HTTP adapter for plugin management; no plugin-specific routes."""
from __future__ import annotations

import json
import mimetypes
from urllib.parse import parse_qs, unquote, urlparse


def route(handler, state, respond, method):
    parsed = urlparse(handler.path)
    path = unquote(parsed.path)
    if not (path.startswith(('/api/plugins', '/api/hooks', '/api/embedding-models', '/plugins/', '/api/plugin-events',
                             '/api/plugin-host', '/api/model-requests'))):
        return False
    try:
        manager = state.get('plugins')
        if manager is None:
            respond(handler, 503, {'error': '插件服务尚未初始化'})
            return True
        query = parse_qs(parsed.query)
        if method == 'GET' and path == '/api/embedding-models':
            respond(handler, 200, {'models': [{**m, 'has_key': state['store'].has_key('embedding:' + m['id'])} for m in manager.runtime.models.embeddings()]})
        elif method == 'GET' and path.startswith('/api/plugins/') and path.endswith('/runtime'):
            respond(handler, 200, manager.runtime.snapshot(path.split('/')[3]))
        elif method == 'GET' and path == '/api/hooks':
            respond(handler, 200, manager.hooks.snapshot())
        elif method == 'GET' and path == '/api/plugins':
            respond(handler, 200, manager.snapshot())
        elif method == 'GET' and path.startswith('/plugins/'):
            parts = path.split('/', 3)
            target = manager.asset(parts[2], parts[3])
            body = target.read_bytes()
            handler.send_response(200)
            handler.send_header('Content-Type', mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            handler.send_header('Content-Length', str(len(body)))
            handler.send_header('Cache-Control', 'no-store')
            handler.end_headers()
            handler.wfile.write(body)
        elif method == 'GET' and path.startswith('/api/model-requests'):
            history = state['request_history']
            if path == '/api/model-requests':
                result = history.list(owner_id=query.get('owner_id', [None])[-1] or None,
                    kind=query.get('kind', [None])[-1] or None,
                    limit=min(100, max(1, int(query.get('limit', ['50'])[-1]))),
                    before=query.get('before', [None])[-1] or None,
                    **{k: query.get(k, [None])[-1] or None for k in ('plugin_id', 'dialogue_id', 'job_id', 'purpose')})
            else:
                result = history.get(path.rsplit('/', 1)[-1])
                if result is None:
                    respond(handler, 404, {'error': '请求记录不存在或已到期'})
                    return True
            respond(handler, 200, result)
        elif method == 'GET' and path.startswith('/api/plugin-events/'):
            result = manager.events.get(path.rsplit('/', 1)[-1])
            respond(handler, 200 if result else 404, result or {'error': '消息不存在'})
        elif method in ('POST', 'PUT'):
            content = handler.rfile.read(int(handler.headers.get('Content-Length', '0')))
            if path == '/api/plugins/import':
                result = manager.import_zip(content, query.get('replace', ['0'])[-1] == '1')
                respond(handler, 409 if result.get('conflict') else 200, result)
                return True
            body = json.loads(content or b'{}')
            if path == '/api/embedding-models':
                result = {'models': manager.runtime.models.save_embedding(body)}
            elif path == '/api/embedding-models/delete':
                result = {'models': manager.runtime.models.delete_embedding(body['id'])}
            elif path.startswith('/api/plugins/') and path.endswith('/runtime'):
                owner = path.split('/')[3]
                action = body['method']
                if action not in ('jobs.cancel', 'jobs.retry', 'operations.cancel', 'dialogues.cancel', 'dialogues.history'):
                    raise ValueError('此管理接口只允许查看对话、取消或重试')
                result = manager.runtime.invoke(owner, action, body.get('params', {}))
            elif path == '/api/hooks/settings':
                result = manager.hooks.configure(body['name'], body['enabled'],
                    body.get('plugin_id'), body.get('key'))
            elif path == '/api/plugins/scan':
                result = manager.scan()
            elif path.startswith('/api/plugins/'):
                parts = path.split('/')
                plugin_id, action = parts[3:5]
                if action == 'commands':
                    result = manager.operate(plugin_id, body.get('operation'))
                elif action == 'settings':
                    result = manager.configure(plugin_id, body)
                elif action == 'call':
                    result = manager.call(plugin_id, body.get('action', ''), body.get('values', {}))
                elif action == 'pages' and len(parts) == 6:
                    result = manager.page(plugin_id, parts[5], body)
                else:
                    raise ValueError('未知插件接口')
            elif path.startswith('/api/plugin-events/'):
                result = manager.receive(path.rsplit('/', 1)[-1], body)
                respond(handler, 202, {'accepted': True, **result})
                return True
            elif path.startswith('/api/plugin-host/'):
                parts = path.split('/')
                plugin_id, mode = parts[3:5]
                if mode == 'query':
                    result = manager.query(plugin_id, body['topic'], **body.get('arguments', {}))
                elif mode == 'command':
                    with manager.lease(plugin_id):
                        result = manager.command(plugin_id, body['action'], **body.get('arguments', {}))
                elif mode == 'subscribe':
                    manager.events.subscribe(plugin_id, body.get('types', []))
                    result = {'subscribed': True}
                else:
                    raise ValueError('未知宿主接口')
            else:
                raise ValueError('未知插件接口')
            respond(handler, 200, result)
        else:
            respond(handler, 404, {'error': '插件接口不存在'})
    except (ValueError, KeyError, OSError) as error:
        respond(handler, 400, {'error': str(error)})
    except Exception as error:  # noqa: BLE001 -- plugin failures need an HTTP result.
        respond(handler, 500, {'error': str(error)})
    return True

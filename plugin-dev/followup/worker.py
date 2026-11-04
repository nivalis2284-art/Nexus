"""Isolated stdio worker. The shared SDK is included by the plugin packager."""
import sys

from engine import Engine
from nexus_host import HostClient, serve
from page import Page

host = HostClient()
engine = None
page = None


def initialize():
    global engine, page
    if engine is None:
        engine = Engine(host, sys.argv[1])
        page = Page(engine)
        engine.start()


def manage(action, arguments=None, request_id=None):
    args = arguments or {}
    if action == 'list':
        return {'arrangements': engine.list(), 'scheduler_error': engine.last_error}
    if action == 'read':
        return engine.read(**args)
    if action == 'save':
        return engine.save(**args, request_id=request_id)
    if action == 'control':
        return engine.control(**args, request_id=request_id)
    if action == 'result':
        return engine.result(**args)
    raise ValueError('未知操作')


if __name__ == '__main__':
    try:
        serve(tools=[{'name': 'manage', 'description': 'Internal arrangement operations', 'inputSchema': {'type': 'object'}},
                     {'name': 'page', 'description': 'Internal host page', 'inputSchema': {'type': 'object'}}],
              handlers={'manage': lambda args: manage(**args), 'page': lambda args: page.render(args['request'])}, initialize=initialize)
    finally:
        if engine:
            engine.close()

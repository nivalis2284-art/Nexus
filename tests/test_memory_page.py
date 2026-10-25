"""The standalone memory page: static safety and the documented RPC flow."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest


PAGE = Path(__file__).resolve().parents[1] / 'plugins' / 'memory' / 'web' / 'index.html'


@pytest.fixture(scope='module')
def page():
    assert PAGE.is_file()
    return PAGE.read_text(encoding='utf-8')


def test_page_is_standalone_and_builds_content_without_inner_html(page):
    assert '<script src=' not in page
    assert '<link ' not in page
    assert 'innerHTML' not in page
    assert 'document.write' not in page
    assert page.count('<style>') == 1
    for marker in ('设置与状态', '记忆', '召回记录', '反馈与导出'):
        assert marker in page
    assert '/api/plugins/memory/call' in page.replace('PLUGIN_ID = "memory"',
        '"/api/plugins/memory/call"')


class FakePageServer:
    """Serve the page and answer its plugin RPC calls with contract-shaped data."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.build_running = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if urlparse(self.path).path == '/page':
                    body = PAGE.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if urlparse(self.path).path != '/api/plugins/memory/call':
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.calls.append(payload)
                action = payload['action']
                values = payload.get('values') or {}
                status = owner.status()
                if action == 'status':
                    value = status
                elif action == 'configure':
                    if 'api_key' in values:
                        assert values['api_key'] != ''
                    value = {**status, 'settings': {**status['settings'],
                        'session_id': values.get('session_id', ''),
                        'takeover': values.get('takeover', False),
                        'budget_tokens': values.get('budget_tokens', 3000)}}
                elif action == 'import':
                    assert values.get('session_id', '') == ''
                    value = {'session_id': 'ling-1', 'raw_count': 3, 'records_hash': 'h'}
                elif action == 'build':
                    owner.build_running = True
                    value = {'running': True, 'error': '', 'result': None}
                elif action == 'activate':
                    assert values['version_id'] == 'v1'
                    value = status
                elif action == 'search':
                    assert values['query'] == '问题'
                    value = {'query': values['query'], 'version_id': 'v0', 'mode': 'lexical',
                             'events': [{'kind': 'event', 'id': 'e1', 'title': '旧记忆',
                                         'summary': '摘要', 'at': '2026-10-01',
                                         'source_ids': ['s1']}],
                             'groups': [{'kind': 'group', 'id': 'g1', 'title': '背景',
                                         'summary': '团的概览', 'start_at': 'a',
                                         'end_at': 'b', 'event_ids': ['e1']}],
                             'elapsed_seconds': 0.2}
                elif action == 'expand':
                    assert values['kind'] in ('event', 'group', 'source')
                    assert 'offset' in values and 'limit' in values
                    value = {'kind': values['kind'], 'id': values['id'], 'text': '原文',
                             'offset': values['offset'], 'limit': values['limit'],
                             'total_chars': 2, 'next_offset': None}
                elif action == 'associations':
                    value = {'event_id': values['event_id'], 'version_id': 'v0',
                             'associations': []}
                elif action == 'runs':
                    value = {'runs': [{'id': 'r1', 'status': 'memory', 'at': 1700000000,
                                       'query': '查询', 'material': {}, 'elapsed_seconds': 0.25}],
                             'engine_runs': [{'id': 'r0', 'status': 'done', 'query': '查询',
                                              'elapsed_seconds': 0.5}]}
                elif action == 'feedback':
                    value = {'id': 'feedback-1', 'run_id': values['run_id'],
                             'category': values['category'], 'comment': values.get('comment', '')}
                elif action == 'export':
                    value = {'memory': {'events': []}, 'adapter_runs': []}
                else:
                    self.send_response(400)
                    body = json.dumps({'error': '未知操作'}).encode()
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def status(self):
        return {
            'engine': {'configured': True, 'base_url': 'https://example.test/v1',
                       'model': 'test', 'active_version': 'v0',
                       'versions': [{'id': 'v0', 'status': 'ready'},
                                    {'id': 'v1', 'status': 'ready'}],
                       'counts': {'events': 1, 'groups': 1, 'edges': 0, 'imports': 1},
                       'build_status': ('构建中' if self.build_running else None)},
            'settings': {'takeover': True, 'session_id': 'ling-1', 'budget_tokens': 3000},
            'job': {'running': self.build_running, 'error': '', 'result': None},
        }

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)


def test_page_talks_to_the_documented_plugin_route():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright

    with FakePageServer() as server, sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
        except Exception as error:  # noqa: BLE001 -- optional local Edge.
            pytest.skip('Edge browser unavailable: ' + str(error))
        try:
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{server.http.server_port}/page')
            page.wait_for_selector('#engine-versions .version-row')
            assert page.get_attribute('#api-key', 'value') in (None, '')
            page.fill('#search-query', '问题')
            page.click('#search-button')
            page.wait_for_selector('text=旧记忆')
            page.click('button:has-text("展开事件") >> nth=0')
            page.wait_for_selector('text=原文')
            assert server.calls[-1]['action'] == 'expand'
            page.fill('#api-key', '')
            page.fill('#session-id', 'ling-1')
            page.click('button:has-text("保存连接与设置")')
            page.wait_for_function('document.querySelector("#page-status").textContent.includes("已保存")')
            configure = next(call for call in server.calls if call['action'] == 'configure')
            assert 'api_key' not in configure['values']
            assert configure['values']['session_id'] == 'ling-1'
        finally:
            browser.close()

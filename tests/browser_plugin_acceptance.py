"""Isolated browser acceptance for the plugin center and model context editing.

Run with: .venv\\Scripts\\python.exe tests/browser_plugin_acceptance.py

The server is web.Handler over a temporary ConfigStore, TaskStore and plugin
root. It uses the installed Edge through Playwright, a fixed localhost port and
no model endpoint. Screenshots and a short result log go to
docs/参考/archive/2026-10-25-evidence/plugin-acceptance.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from nexus.core.config import ConfigStore
from nexus.model.request_history import RequestHistory
from nexus.plugins.manager import PluginManager
from nexus.session import SessionStore
from nexus.task import TaskStore
from nexus.web import server as web

from http.server import ThreadingHTTPServer


EVIDENCE = ROOT / "docs" / "参考" / "archive" / "2026-10-25-evidence" / "plugin-acceptance"
PORT = 18766


def plugin_zip() -> bytes:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr(
            "plugin.json",
            json.dumps(
                {
                    "id": "acceptance",
                    "name": "验收插件",
                    "version": "1.0.0",
                    "description": "隔离浏览器验收用的临时插件",
                    "api_version": 1,
                    "entry": "backend.py:create_plugin",
                    "pages": [{"id": "home", "title": "验收页面", "path": "web/index.html"}],
                    "settings_schema": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "title": "显示名称"},
                            "enabled": {"type": "boolean", "title": "页面启用"},
                        },
                    },
                },
                ensure_ascii=False,
            ),
        )
        archive.writestr(
            "backend.py",
            "class Plugin:\n"
            "    def __init__(self, host): self.host = host\n"
            "    def call(self, action, values): return {'action': action, 'values': values}\n"
            "def create_plugin(host): return Plugin(host)\n",
        )
        archive.writestr(
            "web/index.html",
            "<!doctype html><meta charset='utf-8'><title>验收页面</title>"
            "<style>body{font:16px system-ui;padding:24px;background:#f7f5ef;color:#24211c}"
            "h1{margin:0 0 10px}</style><h1 id='plugin-heading'>临时插件页面</h1>"
            "<p id='plugin-copy'>iframe 已从真实插件 ZIP 资源加载。</p>",
        )
    return content.getvalue()


def wait_for_status(manager: PluginManager, status: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = manager.entries.get("acceptance")
        if item is not None and item.status == status:
            return
        time.sleep(0.05)
    item = manager.entries.get("acceptance")
    raise AssertionError(
        "plugin did not reach %s: %s" %
        (status, item and {"status": item.status, "error": item.error})
    )


def run() -> dict:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    # The Windows account's normal Temp directory can deny mkdtemp to the
    # Playwright driver; keep this run's browser artifacts inside the evidence
    # directory instead.
    os.environ["TEMP"] = str(EVIDENCE / "_tmp")
    os.environ["TMP"] = str(EVIDENCE / "_tmp")
    (EVIDENCE / "_tmp").mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    console_errors: list[str] = []
    http_errors: list[dict] = []
    server_requests: list[str] = []
    browser = None
    playwright = None
    http = None
    thread = None
    manager = None
    with tempfile.TemporaryDirectory(prefix="nexus-plugin-browser-") as temporary:
        temp = Path(temporary)
        home = temp / "home"
        workspace = temp / "workspace"
        workspace.mkdir()
        config = ConfigStore(home)
        config.ensure()
        config.save_providers([])
        config.write_config(
            {
                **config.read_config(),
                "providers": [
                    {
                        "id": "prov-acceptance",
                        "name": "本地验收提供商",
                        "family": "openai_chat_completions",
                        "base_url": "http://127.0.0.1:9",
                        "models": [
                            {
                                "id": "acceptance-model",
                                "context_window_tokens": 16000,
                                "max_output_tokens": 1024,
                            }
                        ],
                    }
                ],
                "current": {"provider": "prov-acceptance", "model": "acceptance-model"},
                "workspace": str(workspace),
            }
        )
        manager = PluginManager({"store": config}, root=temp / "plugins")
        history = RequestHistory(home / "requests.sqlite3")
        tasks = TaskStore(home / "tasks.sqlite3")
        state = {
            "store": config,
            "plugins": manager,
            "request_history": history,
            "tasks": tasks,
            "task_controller": SimpleNamespace(active_task=None, queued_ids=lambda: []),
            "sessions": SessionStore(config.sessions_dir),
            "mcp": SimpleNamespace(tools={}, servers={}, status=lambda: []),
        }
        original_state = web.STATE
        web.STATE = state
        http = ThreadingHTTPServer(("127.0.0.1", PORT), web.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%d" % PORT
        page = None
        try:
            playwright = sync_playwright().start()
            if True:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()

                def on_console(message):
                    if message.type == "error":
                        console_errors.append(message.text + " @ " + str(message.location))

                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", on_console)
                page.on("request", lambda request: server_requests.append(request.url))
                page.on(
                    "response",
                    lambda response: http_errors.append(
                        {"url": response.url, "status": response.status}
                    ) if response.status >= 400 else None,
                )
                response = page.goto(base + "/", wait_until="networkidle", timeout=20000)
                assert response and response.status == 200, response
                page.locator("#ling-state-text").filter(has_text="随时可以继续").wait_for(timeout=10000)
                page.screenshot(path=str(EVIDENCE / "00-main-page.png"), full_page=True)

                page.locator("#plugins-open").click()
                page.locator("#plugins-dialog[open]").wait_for()
                page.locator("#plugins-file").set_input_files(
                    {"name": "acceptance.zip", "mimeType": "application/zip", "buffer": plugin_zip()}
                )
                page.locator("#plugins-status").filter(has_text="插件已导入").wait_for(timeout=10000)
                page.locator("#plugins-list .plugin-list-item").filter(has_text="验收插件").wait_for()
                page.locator("#plugin-detail-title").filter(has_text="验收插件").wait_for()
                page.locator("#plugin-detail-actions button[data-operation='load']").click()
                page.locator("#plugin-detail-actions button[data-operation='unload']").wait_for(timeout=15000)
                wait_for_status(manager, "ready")
                page.locator("#plugin-detail-state").filter(has_text="已加载").wait_for(timeout=10000)

                page.locator("#plugin-settings-fields input[type='text']").fill("浏览器验收设置")
                page.locator("#plugin-settings-save").click()
                page.locator("#plugins-status").filter(has_text="设置已保存").wait_for(timeout=10000)
                assert manager.entries["acceptance"].settings["label"] == "浏览器验收设置"

                page.locator("#plugin-detail-pages .plugin-page-row button").click()
                page.locator("#plugin-frame-panel").wait_for()
                frame = page.frame_locator("#plugin-frame")
                frame.locator("#plugin-heading").filter(has_text="临时插件页面").wait_for(timeout=10000)
                frame.locator("#plugin-copy").filter(has_text="iframe").wait_for()
                page.screenshot(path=str(EVIDENCE / "10-plugin-loaded-frame.png"), full_page=True)
                page.locator("#plugin-frame-close").click()
                page.locator("#plugin-frame-panel").wait_for(state="hidden")
                page.locator("#plugins-close").click()
                page.locator("#plugins-dialog").wait_for(state="hidden")

                page.locator("#settings").click()
                page.locator("#settings-dialog[open]").wait_for()
                page.locator("#provider-list .provider-row .pedit").click()
                page.locator("#provider-dialog[open]").wait_for()
                page.locator("#provider-title").filter(has_text="编辑提供商").wait_for()
                page.locator(".provider-model-window-input").fill("32768")
                page.locator("#p-save").click()
                page.locator("#provider-dialog").wait_for(state="hidden")
                page.locator("#context-window-info").filter(has_text="32,768").wait_for(timeout=10000)
                assert config.providers()[0].models[0]["context_window_tokens"] == 32768
                page.screenshot(path=str(EVIDENCE / "20-provider-context.png"), full_page=True)

                unexpected = [
                    url for url in server_requests
                    if not url.startswith(base + "/")
                ]
                assert not unexpected, unexpected
                assert not errors, errors
                business_http_errors = [
                    entry for entry in http_errors
                    if not entry["url"].endswith("/favicon.ico")
                ]
                assert not business_http_errors, business_http_errors

                result = {
                    "ok": True,
                    "url": base,
                    "plugin_status": manager.entries["acceptance"].status,
                    "plugin_settings": manager.entries["acceptance"].settings,
                    "provider_models": config.providers()[0].models,
                    "page_errors": errors,
                    "console_errors": console_errors,
                    "http_errors": http_errors,
                    "local_request_count": len(server_requests),
                }
                (EVIDENCE / "RESULT.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                return result
        finally:
            if browser is not None:
                browser.close()
            if playwright is not None:
                playwright.stop()
            if http is not None:
                http.shutdown()
                http.server_close()
            if thread is not None:
                thread.join(timeout=3)
            if manager is not None:
                manager.close()
            web.STATE = original_state


if __name__ == "__main__":
    try:
        print(json.dumps(run(), ensure_ascii=False, indent=2))
    except Exception as error:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "RESULT.json").write_text(
            json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise

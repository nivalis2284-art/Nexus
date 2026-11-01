import base64
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from nexus.mcp import MCPRegistry
from nexus.plugins.manager import PluginManager
from nexus.tools import build


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<title>Browser test</title><input aria-label="Name"><button onclick="document.body.append(\'Done\')">Go</button>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_packaged_browser_plugin(tmp_path, monkeypatch):
    monkeypatch.setattr("nexus.cache.CACHE", tmp_path / "cache")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    home = tmp_path / "home"
    home.mkdir()
    mcp = MCPRegistry()
    manager = PluginManager({"store": SimpleNamespace(home=home), "mcp": mcp}, root=tmp_path / "plugins")
    archive = next(Path(__file__).parents[1].glob("nexus-browser-control-*.zip"))
    try:
        manager.import_zip(archive.read_bytes())
        manager.load("browser-control")
        deadline = time.monotonic() + 180
        item = manager.entries["browser-control"]
        while item.status == "loading" and time.monotonic() < deadline:
            time.sleep(.2)
        assert item.status == "ready", item.error
        registry, context = build(root=tmp_path)
        context.agent = SimpleNamespace(profile=SimpleNamespace(capabilities=SimpleNamespace(images=True)))
        manager.decorate(registry, context, {"kind": "ling", "owner_id": "test"})
        def call(action, value="", ref=""):
            result = registry.execute("browser_control", {"action": action, "value": value, "ref": ref}, context)
            assert result.ok, result.content
            return result
        page = call("open", f"http://127.0.0.1:{server.server_port}/")
        assert page.data["title"] == "Browser test"
        input_ref = next(e["ref"] for e in page.data["elements"] if e["tag"] == "input")
        page = call("fill", "Nexus", input_ref)
        button_ref = next(e["ref"] for e in page.data["elements"] if e["tag"] == "button")
        assert "Done" in call("click", ref=button_ref).content
        image = registry.execute("browser_screenshot", {}, context)
        assert image.ok, image.content
        assert base64.b64decode(image.data["image"]["data"]).startswith(b"\xff\xd8")
        call("new_tab")
        assert len(call("tabs").data["tabs"]) == 2
        call("switch", "0")
        call("close")
    finally:
        manager.close()
        mcp.close()
        server.shutdown()
        server.server_close()

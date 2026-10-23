import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request

import pytest

from nexus.core.config import ConfigStore
from nexus.file_io import replace_text
from nexus.session import SessionStore
from nexus.tools import web


def test_concurrent_config_reads_and_saves(tmp_path):
    store = ConfigStore(tmp_path)
    store.write_config({"value": 0})
    stop = threading.Event()
    def read():
        while not stop.is_set():
            assert "value" in store.read_config()
    with ThreadPoolExecutor(max_workers=3) as pool:
        readers = [pool.submit(read) for _ in range(2)]
        try:
            for value in range(300):
                store.write_config({"value": value})
        finally:
            stop.set()
        for reader in readers:
            reader.result()
    assert store.read_config()["value"] == 299
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_session_reads_and_renames(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create("test")
    stop = threading.Event()
    def read():
        while not stop.is_set():
            assert store.load(session.id) is not None
    with ThreadPoolExecutor(max_workers=3) as pool:
        readers = [pool.submit(read) for _ in range(2)]
        try:
            for value in range(300):
                assert store.rename(session.id, str(value))
        finally:
            stop.set()
        for reader in readers:
            reader.result()
    assert store.load(session.id).title == "299"
    assert not list(tmp_path.glob("*.tmp"))


def test_replace_cleans_failed_temporary_and_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("original")
    def fail(*args):
        raise PermissionError("not writable")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError):
        replace_text(path, "new")
    assert path.read_text() == "original"
    assert list(tmp_path.iterdir()) == [path]


def test_replace_retries_windows_sharing_failure(tmp_path, monkeypatch):
    original = Path.replace
    attempts = []
    def replace(source, destination):
        attempts.append(source.name)
        if len(attempts) == 1:
            error = PermissionError("sharing violation")
            error.winerror = 32
            raise error
        return original(source, destination)
    monkeypatch.setattr(Path, "replace", replace)
    replace_text(tmp_path / "file", "saved")
    assert len(attempts) == 2
    assert (tmp_path / "file").read_text() == "saved"


@pytest.mark.parametrize("url,private", [
    ("http://127.0.0.1/private", True),
    ("https://unlisted.test/private", False),
])
def test_redirect_is_rejected_before_following(monkeypatch, url, private):
    monkeypatch.setattr(web, "_is_private", lambda host: private)
    handler = web._FetchRedirectHandler(["public.test", "127.0.0.1"])
    with pytest.raises(PermissionError):
        handler.redirect_request(Request("https://public.test"), None, 302, "Found", {}, url)


def test_allowed_redirect_still_follows(monkeypatch):
    monkeypatch.setattr(web, "_is_private", lambda host: False)
    handler = web._FetchRedirectHandler(["public.test"])
    redirected = handler.redirect_request(Request("https://public.test"), None, 302, "Found", {},
                                         "https://public.test/next")
    assert redirected.full_url == "https://public.test/next"


def test_real_redirect_does_not_contact_disallowed_destination(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/secret")
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Permit only the initial local test endpoint; the redirect must be checked.
    monkeypatch.setattr(web, "_is_private", lambda host: host != "127.0.0.1")
    try:
        with pytest.raises(PermissionError):
            web.fetch(SimpleNamespace(web_allowlist=["127.0.0.1", "localhost"]),
                      {"url": f"http://127.0.0.1:{server.server_port}/start", "max_chars": 1000})
        assert seen == ["/start"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

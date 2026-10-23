"""Real HTTP contract for persona sections, entries and the summary draft."""

from __future__ import annotations

import importlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from nexus.model import APIFamily
from nexus.web import persona_api
from nexus.web import server as web


def request(
    base: str, path: str, method: str = "GET", payload: dict[str, Any] | None = None
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    item = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(item, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            value = json.loads(raw)
        except ValueError:
            value = {"error": raw.decode("utf-8", errors="replace")}
        return error.code, value


@dataclass
class Live:
    home: Path
    store: web.ConfigStore
    base: str
    httpd: ThreadingHTTPServer
    thread: threading.Thread
    mcp: Any
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        close = getattr(self.mcp, "close", None)
        if close is not None:
            close()


def start_server(home: Path, monkeypatch: pytest.MonkeyPatch) -> Live:
    store = web.ConfigStore(home)
    store.ensure()
    mcp = web.MCPRegistry()
    monkeypatch.setattr(
        web,
        "STATE",
        {
            "store": store,
            "sessions": web.SessionStore(store.sessions_dir),
            "skills": {},
            "mcp": mcp,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    return Live(home=home, store=store, base=base, httpd=httpd, thread=thread, mcp=mcp)


def no_model(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("persona HTTP tests must not contact a model")


@pytest.fixture()
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A summary route that bypasses the fake must fail instead of reaching a
    # real provider from a test run.
    monkeypatch.setattr(httpx, "AsyncClient", no_model)
    monkeypatch.setattr(httpx, "Client", no_model)
    instance = start_server(tmp_path / "home", monkeypatch)
    try:
        yield instance
    finally:
        instance.close()


def patch_summary(
    monkeypatch: pytest.MonkeyPatch,
    function: Callable[..., Any],
) -> None:
    module = importlib.import_module("nexus.web.persona_api")
    monkeypatch.setattr(module, "generate_summary", function)


def persona_doc() -> dict[str, Any]:
    return {
        "sections": [
            {"id": "core", "title": "核心设定", "content": "核心内容：我是灵。"},
            {"id": "principles", "title": "回复原则", "content": "先给结论，再补充细节。"},
        ],
        "entries": [
            {
                "id": "voice",
                "name": "voice",
                "description": "摘要：先给结论。",
                "body": "正文：先给结论，再补充必要细节。",
                "enabled": True,
            },
            {
                "id": "history",
                "name": "history",
                "description": "摘要：停用的经历。",
                "body": "正文：停用经历的详细内容。",
                "enabled": False,
            },
            {
                "id": "pace",
                "name": "pace",
                "description": "摘要：保持简洁。",
                "body": "正文：保持简洁的详细说明。",
                "enabled": True,
            },
        ],
    }


def assert_snapshot(snapshot: dict[str, Any], document: dict[str, Any]) -> None:
    assert {"persona", "preview", "last_request"} <= set(snapshot)
    persona = snapshot["persona"]
    assert len(persona["sections"]) == len(document["sections"])
    for actual_section, expected_section in zip(
        persona["sections"], document["sections"], strict=True
    ):
        assert actual_section["title"] == expected_section["title"]
        assert actual_section["content"] == expected_section["content"]
        if expected_section.get("id"):
            assert actual_section["id"] == expected_section["id"]
    actual = {item["name"]: item for item in persona["entries"]}
    expected = {item["name"]: item for item in document["entries"]}
    assert set(actual) == set(expected)
    for entry_name, item in expected.items():
        for field in ("name", "description", "body", "enabled"):
            assert actual[entry_name][field] == item[field]
    assert isinstance(snapshot["preview"], str)


def put_doc(live: Live, document: dict[str, Any]) -> dict[str, Any]:
    status, snapshot = request(live.base, "/api/persona", "PUT", document)
    assert status == 200, snapshot
    assert_snapshot(snapshot, document)
    # New sections and a renamed entry receive their canonical ids from the
    # server. Keep the local document aligned for the next request.
    document["sections"] = [dict(item) for item in snapshot["persona"]["sections"]]
    document["entries"] = [dict(item) for item in snapshot["persona"]["entries"]]
    return snapshot


def configure_model(store: web.ConfigStore) -> web.Settings:
    settings = store.load()
    store.update_provider(
        settings.provider.id,
        {
            "base_url": "https://fake.test/v1",
            "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
            "models": [
                {
                    "id": "fake",
                    "context_window_tokens": 32000,
                    "max_output_tokens": 1024,
                }
            ],
        },
    )
    store.select(settings.provider.id, "fake", "high")
    store.write_credential(settings.provider.id, "test-key")
    return store.load()


def test_crud_sections_entry_files_and_storage(live: Live) -> None:
    status, initial = request(live.base, "/api/persona")
    assert status == 200, initial
    assert initial["persona"]["sections"] == [
        {"id": "core", "title": "核心设定", "content": ""},
        {"id": "reply-principles", "title": "回复原则", "content": ""},
    ]
    assert initial["persona"]["entries"] == []
    assert initial["last_request"] is None

    document = persona_doc()
    snapshot = put_doc(live, document)
    preview = snapshot["preview"]
    assert "核心内容：我是灵。" in preview
    assert "先给结论，再补充细节。" in preview
    assert "摘要：先给结论。" in preview
    assert "摘要：保持简洁。" in preview
    assert "摘要：停用的经历。" not in preview
    assert "正文：先给结论" not in preview
    assert "正文：停用经历" not in preview

    stored = live.home / "persona.json"
    raw = json.loads(stored.read_text(encoding="utf-8"))
    assert raw["sections"] == document["sections"]
    assert raw["entries"][0] == {"name": "voice", "enabled": True}
    skill = live.home / "persona-skills" / "voice" / "SKILL.md"
    assert skill.is_file()
    assert "正文：先给结论，再补充必要细节。" in skill.read_text(encoding="utf-8")
    assert "正文：先给结论" not in stored.read_text(encoding="utf-8")

    document["sections"][0]["content"] = "核心内容 v2"
    document["sections"].insert(1, {"title": "新栏目", "content": "新栏目内容"})
    document["entries"][0].update(
        name="voice-v2", description="摘要：更新后的表达。", body="正文：更新后的详细表达。"
    )
    document["entries"][1]["enabled"] = True
    snapshot = put_doc(live, document)
    preview = snapshot["preview"]
    assert "核心内容 v2" in preview
    assert "新栏目内容" in preview
    assert "摘要：更新后的表达。" in preview
    assert "摘要：停用的经历。" in preview
    assert "正文：更新后的详细表达。" not in preview
    assert document["sections"][1]["id"]
    assert not (live.home / "persona-skills" / "voice" / "SKILL.md").exists()
    renamed = live.home / "persona-skills" / "voice-v2" / "SKILL.md"
    assert "正文：更新后的详细表达。" in renamed.read_text(encoding="utf-8")

    document["sections"] = [document["sections"][1], document["sections"][0]]
    document["sections"][1]["title"] = "核心设定已改名"
    document["sections"].pop(0)
    document["entries"][0]["enabled"] = False
    document["entries"][1]["enabled"] = False
    snapshot = put_doc(live, document)
    preview = snapshot["preview"]
    assert "核心设定已改名" in preview
    assert "新栏目内容" not in preview
    assert "摘要：更新后的表达。" not in preview
    assert "摘要：停用的经历。" not in preview

    document["entries"] = [document["entries"][0]]
    document["entries"][0]["enabled"] = True
    snapshot = put_doc(live, document)
    assert [item["id"] for item in snapshot["persona"]["entries"]] == ["voice-v2"]
    assert "摘要：更新后的表达。" in snapshot["preview"]
    assert "摘要：停用的经历。" not in snapshot["preview"]
    assert not (live.home / "persona-skills" / "history" / "SKILL.md").exists()
    assert not (live.home / "persona-skills" / "pace" / "SKILL.md").exists()

    status, fetched = request(live.base, "/api/persona")
    assert status == 200, fetched
    assert_snapshot(fetched, document)
    assert fetched["last_request"] is None


def test_rejects_old_document_shape(live: Live) -> None:
    status, error = request(
        live.base,
        "/api/persona",
        "PUT",
        {"core": "旧核心", "entries": [{"name": "old", "summary": "旧摘要", "body": "旧正文"}]},
    )
    assert status == 400, error
    assert "sections" in str(error.get("error") or "")
    assert not (live.home / "persona-skills").exists()

    status, error = request(
        live.base,
        "/api/persona",
        "PUT",
        {"sections": [], "entries": [{"name": "old", "summary": "旧摘要"}]},
    )
    assert status == 400, error
    assert "description" in str(error.get("error") or "")


def test_legacy_nine_entries_migrate_on_load(live: Live) -> None:
    legacy = [
        ("voice", "自然直接的表达", "先给结论。", "正文：自然直接的表达。", True),
        ("judgment", "温和的独立判断", "表达有依据的不同意见。", "正文：独立判断。", True),
        ("care", "关心与分寸", "先听清楚再回应。", "正文：关心与分寸。", True),
        ("curiosity", "有落点的好奇心", "追问具体的原因。", "正文：好奇心。", True),
        ("taste", "雨夜、音乐与日常审美", "偏爱有余味的表达。", "正文：审美。", True),
        ("humor", "轻微的幽默感", "偶尔缓和气氛。", "正文：幽默。", True),
        ("relationship", "与 Nivalis 的相处", "把对方视为长期伙伴。", "正文：相处。", True),
        ("work-style", "认真而克制地做事", "先做成，再解释。", "正文：做事。", True),
        ("dialogue-examples", "交流情境示例", "示例只展示语气。", "正文：示例。", False),
    ]
    old = {
        "core": "旧核心：我是灵。",
        "entries": [
            {
                "id": entry_id,
                "name": name,
                "summary": summary,
                "body": body,
                "enabled": enabled,
            }
            for entry_id, name, summary, body, enabled in legacy
        ],
    }
    path = live.home / "persona.json"
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    status, snapshot = request(live.base, "/api/persona")
    assert status == 200, snapshot
    persona = snapshot["persona"]
    assert [item["id"] for item in persona["sections"]] == ["core", "reply-principles"]
    assert persona["sections"][0]["content"] == "旧核心：我是灵。"
    assert persona["sections"][1]["content"] == ""
    assert [item["name"] for item in persona["entries"]] == [
        entry_id for entry_id, *_ in legacy
    ]
    for index, (entry_id, _name, summary, body, enabled) in enumerate(legacy):
        entry = persona["entries"][index]
        assert entry["id"] == entry_id
        assert entry["description"] == summary
        assert entry["body"] == body
        assert entry["enabled"] is enabled
        skill = live.home / "persona-skills" / entry_id / "SKILL.md"
        assert skill.is_file()
        assert body in skill.read_text(encoding="utf-8")
    assert (live.home / "persona.json.legacy-backup").is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["entries"] == [
        {"name": entry_id, "enabled": enabled}
        for entry_id, _name, _summary, _body, enabled in legacy
    ]
    assert "旧核心：我是灵。" in snapshot["preview"]
    assert all(summary in snapshot["preview"] for _, _, summary, _, enabled in legacy if enabled)
    assert legacy[-1][2] not in snapshot["preview"]
    assert "正文：" not in snapshot["preview"]


def test_saved_persona_survives_server_restart(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = persona_doc()
    put_doc(live, document)
    live.close()

    restarted = start_server(live.home, monkeypatch)
    try:
        status, snapshot = request(restarted.base, "/api/persona")
        assert status == 200, snapshot
        assert_snapshot(snapshot, document)
        assert "核心内容：我是灵。" in snapshot["preview"]
        assert "摘要：先给结论。" in snapshot["preview"]
        assert "摘要：停用的经历。" not in snapshot["preview"]
        skill = live.home / "persona-skills" / "voice" / "SKILL.md"
        assert "正文：先给结论，再补充必要细节。" in skill.read_text(encoding="utf-8")
    finally:
        restarted.close()


def test_manual_skill_edit_refreshes_on_next_read(
    live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = persona_doc()
    put_doc(live, document)
    skill = live.home / "persona-skills" / "voice" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    skill.write_text(
        text.replace("摘要：先给结论。", "摘要：手动更新后的摘要。").replace(
            "正文：先给结论，再补充必要细节。", "正文：手动更新后的正文。"
        ),
        encoding="utf-8",
    )

    status, snapshot = request(live.base, "/api/persona")
    assert status == 200, snapshot
    entry = snapshot["persona"]["entries"][0]
    assert entry["description"] == "摘要：手动更新后的摘要。"
    assert entry["body"] == "正文：手动更新后的正文。"
    assert "摘要：手动更新后的摘要。" in snapshot["preview"]

    live.close()
    restarted = start_server(live.home, monkeypatch)
    try:
        status, snapshot = request(restarted.base, "/api/persona")
        assert status == 200, snapshot
        assert snapshot["persona"]["entries"][0]["body"] == "正文：手动更新后的正文。"
    finally:
        restarted.close()


def test_gbk_persona_entry_is_readable_and_saves_as_utf8(live: Live) -> None:
    folder = live.home / "persona-skills" / "gbk-voice"
    folder.mkdir(parents=True)
    path = folder / "SKILL.md"
    original = (
        "---\n"
        "name: gbk-voice\n"
        "description: 旧摘要\n"
        "when: 需要时读取\n"
        "custom:\n"
        "  owner: 团队\n"
        "---\n"
        "中文正文：逐字保留。\n"
    ).encode("gbk")
    path.write_bytes(original)

    status, snapshot = request(live.base, "/api/persona")
    assert status == 200, snapshot
    entry = snapshot["persona"]["entries"][0]
    assert entry["description"] == "旧摘要"
    assert entry["body"] == "中文正文：逐字保留。\n"
    assert path.read_bytes() == original

    document = snapshot["persona"]
    document["entries"][0]["body"] = "页面保存后的中文正文。\n"
    status, saved = request(live.base, "/api/persona", "PUT", document)
    assert status == 200, saved
    assert saved["persona"]["entries"][0]["body"] == "页面保存后的中文正文。\n"
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert "中文正文：逐字保留。" not in text
    assert "when: 需要时读取\n" in text
    assert "custom:\n  owner: 团队\n" in text


def test_undecodable_persona_entry_returns_error_and_is_not_overwritten(live: Live) -> None:
    folder = live.home / "persona-skills" / "broken-voice"
    folder.mkdir(parents=True)
    path = folder / "SKILL.md"
    original = (
        b"---\nname: broken-voice\ndescription: broken\n---\n"
        b"valid ascii\xff"
    )
    path.write_bytes(original)

    status, error = request(live.base, "/api/persona")
    assert status == 400
    assert "UTF-8" in error["error"] and "GBK" in error["error"]
    assert path.read_bytes() == original

    status, error = request(
        live.base,
        "/api/persona",
        "PUT",
        {"sections": [], "entries": [{
            "name": "broken-voice", "description": "broken",
            "body": "attempted save", "enabled": True,
        }]},
    )
    assert status == 400
    assert "UTF-8" in error["error"] and "GBK" in error["error"]
    assert path.read_bytes() == original


def test_summary_is_only_a_draft(live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = configure_model(live.store)
    document = persona_doc()
    put_doc(live, document)
    stored = live.home / "persona.json"
    before = stored.read_bytes()
    skill = live.home / "persona-skills" / "voice" / "SKILL.md"
    skill_before = skill.read_bytes()
    last_path = live.home / "persona-last-request.json"
    last_before = last_path.read_bytes() if last_path.exists() else None
    seen: list[tuple[web.Settings, str, str]] = []

    async def fake(settings: web.Settings, name: str, body: str, **kwargs) -> str:
        seen.append((settings, name, body))
        return "建议摘要：直接一些。"

    patch_summary(monkeypatch, fake)
    status, result = request(
        live.base,
        "/api/persona/summary",
        "POST",
        {"name": "voice", "body": "这是只用于生成建议的草稿正文。"},
    )
    assert status == 200, result
    assert result["summary"] == "建议摘要：直接一些。"
    assert len(seen) == 1
    current, name, body = seen[0]
    assert name == "voice"
    assert body == "这是只用于生成建议的草稿正文。"
    assert current.model["id"] == "fake"
    assert current.effort == "high"
    assert current.api_key == "test-key"
    assert current.profile.model == "fake"
    assert current.profile.base_url == "https://fake.test/v1"
    assert settings.effort == "high"

    assert stored.read_bytes() == before
    assert skill.read_bytes() == skill_before
    assert (last_path.read_bytes() if last_path.exists() else None) == last_before
    status, snapshot = request(live.base, "/api/persona")
    assert status == 200, snapshot
    assert_snapshot(snapshot, document)
    assert snapshot["last_request"] is None


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ValueError("草稿无效"), 400),
        (persona_api.SummaryGenerationError("upstream failed"), 502),
    ],
)
def test_summary_failure_preserves_persona(
    live: Live,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: int,
) -> None:
    configure_model(live.store)
    document = persona_doc()
    put_doc(live, document)
    stored = live.home / "persona.json"
    before = stored.read_bytes()
    last_path = live.home / "persona-last-request.json"
    last_before = last_path.read_bytes() if last_path.exists() else None

    async def broken(settings: web.Settings, name: str, body: str, **kwargs) -> str:
        raise failure

    patch_summary(monkeypatch, broken)
    status, error = request(
        live.base,
        "/api/persona/summary",
        "POST",
        {"name": "voice", "body": "草稿"},
    )
    assert status == expected, error
    assert error.get("error")
    assert stored.read_bytes() == before
    assert (last_path.read_bytes() if last_path.exists() else None) == last_before
    status, snapshot = request(live.base, "/api/persona")
    assert status == 200, snapshot
    assert_snapshot(snapshot, document)
    assert snapshot["last_request"] is None


def test_save_without_key_and_summary_error_is_explicit(live: Live) -> None:
    settings = configure_model(live.store)
    live.store.write_credential(settings.provider.id, "")
    settings = live.store.load()
    assert settings.api_key == ""
    assert settings.provider.base_url == "https://fake.test/v1"
    assert settings.model["id"] == "fake"

    document = persona_doc()
    snapshot = put_doc(live, document)
    assert "核心内容：我是灵。" in snapshot["preview"]
    stored = live.home / "persona.json"
    before = stored.read_bytes()
    # The real helper validates configuration before opening a model client.
    # The fixture's no_model replacement would raise if HTTP were attempted.
    status, error = request(
        live.base,
        "/api/persona/summary",
        "POST",
        {"name": "voice", "body": "草稿"},
    )
    assert status == 400, error
    message = str(error.get("error") or "")
    assert message
    assert any(
        token in message.lower()
        for token in ("api", "key", "base url", "配置", "模型")
    )
    assert stored.read_bytes() == before
    status, fetched = request(live.base, "/api/persona")
    assert status == 200, fetched
    assert_snapshot(fetched, document)
    assert fetched["last_request"] is None

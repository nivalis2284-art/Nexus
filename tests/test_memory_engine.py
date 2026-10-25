"""Focused tests for the independent persistent memory service."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest

SERVICE_SRC = Path(__file__).resolve().parents[1] / "services" / "persistent_memory" / "src"
sys.path.insert(0, str(SERVICE_SRC))

from persistent_memory import MemoryEngine, canonical_record, normalize_record, records_hash
from persistent_memory.llm import ModelError, ModelResult
from persistent_memory.retrieval import tokens_for_budget


class FakeModel:
    """Small OpenAI-compatible decision mock with controllable blocking."""

    calls: ClassVar[list[dict]] = []
    gate: threading.Event | None = None
    fail_organization = False
    build_groups_override = None

    def __init__(self, base_url, model, api_key, timeout):
        self.base_url, self.model, self.api_key, self.timeout = base_url, model, api_key, timeout

    def complete_json(self, system, user):
        type(self).calls.append({"system": system, "user": user, "timeout": self.timeout})
        if type(self).gate is not None and "Organize" in system:
            type(self).gate.wait(5)
        if "Organize" in system:
            if type(self).fail_organization:
                raise ModelError("mock organization failure")
            request = json.loads(user)
            ids = [event["id"] for event in request["events"]]
            groups = type(self).build_groups_override or [
                {"title": "Dialogue", "dimension": "topic", "summary": "Conversation",
                 "event_ids": ids},
                {"title": "Project", "dimension": "project", "summary": "Project work",
                 "event_ids": ids[:1]},
            ]
            associations = [{
                "source_id": ids[0], "target_id": ids[-1],
                "relation": "follows", "reason": "Chronological continuation.",
            }] if len(ids) > 1 else []
            return ModelResult({"groups": groups, "associations": associations},
                               {"prompt_tokens": 10, "completion_tokens": 5}, 0.01)
        if "Select memory context" in system:
            request = json.loads(user)
            return ModelResult({
                "event_ids": [item["id"] for item in request["events"][:2]],
                "group_ids": [item["id"] for item in request["groups"][:1]],
            }, {"prompt_tokens": 7, "completion_tokens": 3}, 0.02)
        request = json.loads(user)
        events = []
        for record in request["records"]:
            events.append({
                "title": f"Event {record['source_ref'][:8]}",
                "summary": record["record"].get("text", "")[-300:],
                "source_refs": [record["source_ref"]],
                "before": "", "after": "", "uncertainty": "",
            })
        return ModelResult({"events": events}, {"prompt_tokens": 20, "completion_tokens": 8}, 0.03)


def engine(tmp_path, **kwargs):
    instance = MemoryEngine(tmp_path, model_factory=FakeModel, **kwargs)
    instance.configure({"base_url": "http://mock.local/v1", "model": "mock",
                        "api_key": "secret-key-xyz", "timeout": 5})
    return instance


def records():
    return [
        {"kind": "user", "text": "start project alpha", "at": "2026-01-01T00:00:00Z"},
        {"kind": "tool", "name": "shell", "text": "huge output " * 100,
         "at": "2026-01-01T00:00:01Z", "id": "tool-call-id"},
        {"kind": "assistant", "text": "alpha complete; the final detail is oui",
         "at": "2026-01-01T00:00:02Z", "thinking": "private chain"},
    ]


def test_snapshot_is_exact_normalized_prefix_and_excludes_thinking(tmp_path):
    memory = engine(tmp_path)
    raw = records()
    normalized = [normalize_record(item) for item in raw]
    memory.import_records("s1", raw)
    version = memory.build()
    memory.activate(version["id"])
    snap = memory.snapshot()
    assert snap["records"] == normalized
    assert all("_record_key" not in item for item in snap["records"])
    assert snap["records"][1]["id"] == "tool-call-id"
    assert "thinking" not in snap["records"][2]
    assert snap["records_hash"] == records_hash(normalized)
    assert memory.export()["records"] == normalized


def test_group_overlap_duplicate_merge_and_long_body_tail(tmp_path):
    memory = engine(tmp_path)
    long_text = "long body " * 3000 + "oui"
    raw = [
        {"kind": "user", "text": long_text, "at": "2026-01-01T00:00:00Z"},
        {"kind": "tool", "name": "probe", "text": "bulk " * 2000,
         "at": "2026-01-01T00:00:01Z", "id": "keep-me"},
        {"kind": "assistant", "text": long_text, "at": "2026-01-01T00:00:02Z"},
    ]
    memory.import_records("s1", raw)
    version = memory.build()
    events = memory.store.events(version["id"])
    groups = memory.store.groups(version["id"])
    assert events[0]["text"].endswith("oui")
    assert len(groups) == 2
    assert set(groups[0]["event_ids"]) & set(groups[1]["event_ids"])
    assert groups[0]["dimension"] != groups[1]["dimension"]
    source_events = [event for event in events if "source:" + canonical_record(raw[1])["_record_key"]
                     in event["source_ids"]]
    assert source_events == []
    assert memory.store.version_records(version["id"])[1]["id"] == "keep-me"


def test_chinese_budget_is_strict_and_prepare_context_is_compact(tmp_path):
    memory = engine(tmp_path)
    raw = [{"kind": "user", "text": "中文内容" * 1000, "at": "2026-01-01T00:00:00Z"},
           {"kind": "assistant", "text": "结果", "at": "2026-01-01T00:00:01Z"}]
    memory.import_records("s1", raw)
    version = memory.build()
    memory.activate(version["id"])
    result = memory.prepare("中文内容", budget_tokens=128)
    assert result["estimated_tokens"] <= 128
    assert tokens_for_budget(result["content"], 128) <= 128
    prepare_call = next(call for call in FakeModel.calls if "Select memory context" in call["system"])
    assert "中文内容" * 1000 not in prepare_call["user"]


def test_status_does_not_block_and_failure_keeps_active(tmp_path):
    memory = engine(tmp_path)
    memory.import_records("s1", records())
    first = memory.build()
    memory.activate(first["id"])
    gate = threading.Event()
    FakeModel.gate = gate
    FakeModel.fail_organization = True
    try:
        start = time.monotonic()
        assert memory.start_build()["running"] is True
        assert time.monotonic() - start < 1
        status_start = time.monotonic()
        status = memory.status()
        assert time.monotonic() - status_start < 1
        assert status["build_status"]["running"] is True
        memory.search("", use_model=False)
        gate.set()
        deadline = time.monotonic() + 5
        while memory.status()["build_status"]["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert memory.status()["build_status"]["error"]
        assert memory.status()["active_version"] == first["id"]
        assert not any(item["status"] == "failed" and item["id"] == first["id"]
                       for item in memory.status()["versions"])
    finally:
        gate.set()
        FakeModel.gate = None
        FakeModel.fail_organization = False


def test_reimport_keeps_old_snapshot_and_activation(tmp_path):
    memory = engine(tmp_path)
    memory.import_records("s1", records())
    old = memory.build()
    memory.activate(old["id"])
    old_snapshot = memory.snapshot()
    memory.import_records("s1", [{"kind": "user", "text": "changed", "at": "x"}])
    assert memory.snapshot() == old_snapshot
    new = memory.build()
    memory.activate(new["id"])
    assert memory.snapshot()["records"][0]["text"] == "changed"
    memory.activate(old["id"])
    assert memory.snapshot() == old_snapshot


def test_prepare_model_failure_propagates(tmp_path):
    memory = engine(tmp_path)
    memory.import_records("s1", records())
    version = memory.build()
    memory.activate(version["id"])
    class Broken:
        def __init__(self, *args, **kwargs): pass
        def complete_json(self, *args): raise ModelError("fail")
    memory._model_factory = Broken
    with pytest.raises(ModelError, match="fail"):
        memory.prepare("alpha")


def test_mcp_stdio_roundtrip(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    data = tmp_path / "mcp-data"
    memory = MemoryEngine(data)
    memory.import_records("s1", records())
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "persistent_memory", "--data", str(data)],
        env={"PYTHONPATH": str(SERVICE_SRC)},
    )

    async def exercise():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert "memory_build" in tools and "memory_status" in tools
            # Console-script direct imports and stdio share the same store.
            assert (data / "memory.sqlite3").exists()

    asyncio.run(exercise())


def test_source_expand_accepts_prefixed_source_id(tmp_path):
    memory = engine(tmp_path)
    memory.import_records("s1", records())
    version = memory.build()
    memory.activate(version["id"])
    source_id = "source:" + canonical_record(records()[0])["_record_key"]
    expanded = memory.expand("source", source_id, limit=100)
    assert expanded["id"] == source_id
    assert "start project alpha" in expanded["text"]
    assert "api_key" not in memory.export()["config"]

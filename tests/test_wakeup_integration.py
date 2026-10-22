"""Actual Agent + stdio MCP + HTTP callback; model output alone is scripted."""
import json
import time
from datetime import datetime, timedelta

import httpx

from test_scheduler_bridge import bridge as bridge, wake_runtime as wake_runtime


def tool_response(name, arguments, call_id):
    delta = {"tool_calls": [{"index": 0, "id": call_id, "type": "function",
              "function": {"name": name, "arguments": json.dumps(arguments)}}]}
    frames = [
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        text="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
             + "data: [DONE]\n\n")


def test_agent_books_wakes_in_same_conversation_and_rearms(bridge, monkeypatch):
    import nexus.core.agent as module
    runtime, service, base = bridge
    runtime.state["store"].save_preferences(permission="ask")
    tool_name = next(name for name, (_, declaration) in service.registry.tools.items()
                     if declaration.name == "schedule_wakeup")
    requests, saved = [], {}

    def model(request):
        body = json.loads(request.content)
        requests.append(body)
        step = len(requests)
        if step == 1:
            assert any(tool["function"]["name"] == tool_name for tool in body["tools"])
            saved["due"] = (datetime.now().astimezone() + timedelta(seconds=3)).isoformat(timespec="seconds")
            return tool_response(tool_name, {
                "title": "Revisit two options", "reason": "Allow time to reconsider",
                "continuation": "Compare option B with the owner's original goal",
                "run_at": saved["due"],
            }, "book")
        if step == 2:
            result = json.loads(body["messages"][-1]["content"])
            saved["id"] = result["id"]
            assert result["next_run_at"] == saved["due"]
            return tool_response("automation_finish", {
                "summary": "Saved a future revisit; nothing to report now.",
            }, "finish-first")
        if step == 3:
            material = json.dumps(body, ensure_ascii=False)
            assert "Compare option B with the owner's original goal" in material
            assert saved["id"] in material and saved["due"] in material
            assert "Remember that I prefer useful updates." in material
            return tool_response(tool_name, {
                "id": saved["id"], "title": "Revisit two options",
                "reason": "Need more time", "continuation": "Recheck option B tomorrow",
                "delay_minutes": 60,
            }, "rearm")
        assert step == 4
        return tool_response("automation_finish", {
            "reply": "Reviewed the options and rescheduled the same follow-up.",
        }, "finish-second")

    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(model)))
    session_id = runtime.inbox().session_id
    first = runtime.receive({"reasons": [{"type": "wake"}]})
    runtime._execute_pending()
    first_run = next(r for r in runtime.store.runs() if r["id"] == first["id"])
    assert first_run["status"] == "done", first_run["error"]
    assert len(requests) == 2

    service.call("scheduler_configure", values={
        "enabled": True, "interval_enabled": False, "planning_enabled": False,
    })
    deadline = time.monotonic() + 10
    while runtime.store.next_run() is None and time.monotonic() < deadline:
        time.sleep(.05)
    pending = runtime.store.next_run()
    assert pending is not None, service.snapshot()
    assert pending["reasons"][0]["schedule_id"] == saved["id"]
    assert pending["reasons"][0]["scheduled_for"] == saved["due"]
    runtime._execute_pending()
    second_run = next(r for r in runtime.store.runs() if r["id"] == pending["id"])
    assert second_run["status"] == "done", second_run["error"]
    assert len(requests) == 4
    schedules = service.snapshot()["schedules"]
    rearmed = next(s for s in schedules if s["id"] == saved["id"])
    assert rearmed["enabled"] and rearmed["next_run_at"] > saved["due"]
    assert "Recheck option B tomorrow" in rearmed["prompt"]
    assert len([s for s in schedules if s["origin"] == "agent"]) == 1
    session = runtime.state["sessions"].load(session_id)
    assert [m.kind for m in session.items] == ["user", "experience", "assistant"]
    assert runtime.inbox().session_id == session_id

    # The existing page API can read/edit/pause/delete this same record.
    with httpx.Client(base_url=base) as client:
        snapshot = client.get("/api/automation").json()
        assert any(s["id"] == saved["id"] for s in snapshot["schedules"])
        response = client.post("/api/automation/schedules", json={
            "id": saved["id"], "prompt": "Owner-edited continuation", "enabled": False,
        })
        assert response.status_code == 200, response.text
        paused = next(s for s in response.json()["schedules"] if s["id"] == saved["id"])
        assert not paused["enabled"] and paused["purpose"] == "wake"
        response = client.post("/api/automation/schedules/" + saved["id"] + "/delete", json={})
        assert response.status_code == 200, response.text
        assert not any(s["id"] == saved["id"] for s in response.json()["schedules"])


def test_mcp_invalid_appointment_reports_error_without_saving(bridge):
    runtime, service, _ = bridge
    connection = service.registry.servers[service.name]
    before = service.snapshot()["schedules"]
    result = connection.call_tool("schedule_wakeup", {
        "title": "Bad", "reason": "No time", "continuation": "Do something",
    })
    assert not result.ok
    assert "exactly one" in result.text
    assert service.snapshot()["schedules"] == before


def test_merging_wakes_retains_each_appointment_handoff(wake_runtime):
    runtime = wake_runtime
    for index in (1, 2):
        runtime.receive({"source": "scheduler", "reasons": [{
            "type": "wake", "schedule_id": f"appointment-{index}",
            "scheduled_for": f"2030-01-0{index}T09:00:00+09:00",
            "prompt": f"Reason {index}; continue topic {index}",
        }], "prompt": f"Reason {index}; continue topic {index}"})
    assert len(runtime.store.runs()) == 1
    material = runtime.context(runtime.store.next_run())
    assert "Reason 1; continue topic 1" in material
    assert "Reason 2; continue topic 2" in material
    assert "appointment-1" in material and "appointment-2" in material

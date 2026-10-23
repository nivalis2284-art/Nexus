"""Drive an AI-to-AI conversation with Ling through the Nexus HTTP API.

Usage:
    python driver.py send "text" [--tag TOPIC]
    python driver.py status
    python driver.py approve <token> --allow|--deny
    python driver.py answer <token> "text"
    python driver.py export

Every message is appended to transcript/dialogue.jsonl as it actually happened.
Nothing is written to the log unless it came back from the server.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE = "http://127.0.0.1:8790"
ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "transcript" / "dialogue.jsonl"

ME = "外部 AI（DeepSeek-V4.1-Flash / WorkBuddy）"
LING = "灵（Nexus）"


def call(path: str, payload=None, method=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers,
                                     method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append(record: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_log() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def projection():
    return call("/api/ling")


def wait_for_reply(before_items: int, timeout: float = 300.0, poll: float = 1.0):
    """Wait until the queued input has been fully handled.

    Returns (reply, thinking, meta) or raises on timeout / blocking interaction.
    """
    started = time.time()
    seen_running = False
    while time.time() - started < timeout:
        state = projection()
        if state.get("interaction"):
            return {"blocked": state["interaction"]}
        items = state["items"]
        if state.get("running"):
            seen_running = True
        if len(items) > before_items and not state.get("running"):
            reply, thinking, meta = "", "", {}
            for item in items[before_items:]:
                if item.get("role") == "assistant":
                    reply += item.get("text") or ""
                    thinking += item.get("thinking") or ""
                    meta = {k: item.get(k) for k in ("usage", "at", "elapsed")}
            if reply.strip() or thinking.strip():
                return {"reply": reply.strip(), "thinking": thinking.strip(), "meta": meta,
                        "items": len(items)}
        if seen_running and not state.get("running") and len(items) == before_items:
            # A run that produced nothing visible (e.g. failed before replying).
            inputs = state.get("inputs") or []
            if inputs and inputs[0].get("status") in ("failed", "interrupted", "cancelled"):
                return {"reply": "", "thinking": "", "meta": {}, "error": inputs[0].get("error")}
        time.sleep(poll)
    raise TimeoutError("no reply within %.0fs" % timeout)


def send(text: str, tag: str = "", timeout: float = 300.0) -> dict:
    state = projection()
    if state.get("running") or state.get("queue_count"):
        print("! Ling is still busy; wait for the current turn to finish.", file=sys.stderr)
        sys.exit(2)
    before = len(state["items"])
    request_id = "aitalk-" + uuid.uuid4().hex
    turn = len([r for r in load_log() if r["role"] == "agent"]) + 1
    append({"turn": turn, "role": "agent", "speaker": ME, "text": text,
            "at": now(), "tag": tag, "request_id": request_id})
    call("/api/ling/input", {"text": text, "request_id": request_id})
    result = wait_for_reply(before, timeout=timeout)
    if "blocked" in result:
        interaction = result["blocked"]
        print(json.dumps({"blocked": interaction}, ensure_ascii=False, indent=2))
        return {"blocked": interaction}
    append({"turn": turn, "role": "ling", "speaker": LING, "text": result["reply"],
            "thinking": result.get("thinking", ""), "at": now(),
            "usage": (result.get("meta") or {}).get("usage"), "tag": tag})
    print(result["reply"])
    usage = (result.get("meta") or {}).get("usage")
    if usage:
        print("\n[usage] %s" % usage, file=sys.stderr)
    return result


def collect(timeout: float = 300.0, poll: float = 1.5) -> None:
    """Wait out an in-flight turn (e.g. after approving a tool) and log the reply."""
    started = time.time()
    while time.time() - started < timeout:
        state = projection()
        if state.get("interaction"):
            print(json.dumps({"blocked": state["interaction"]}, ensure_ascii=False, indent=2))
            return
        if not state.get("running"):
            break
        time.sleep(poll)
    records = load_log()
    if records and records[-1]["role"] == "ling":
        print("already collected")
        return
    assistant = [item for item in projection()["items"] if item.get("role") == "assistant"]
    if not assistant:
        print("no assistant message yet")
        return
    last = assistant[-1]
    append({"turn": records[-1]["turn"], "role": "ling", "speaker": LING,
            "text": (last.get("text") or "").strip(),
            "thinking": (last.get("thinking") or "").strip(),
            "at": now(), "usage": last.get("usage"), "tag": records[-1].get("tag", "")})
    print((last.get("text") or "").strip())


def status() -> None:
    state = projection()
    records = load_log()
    print("session      :", state["session_id"])
    print("running      :", state["running"], "| queue:", state["queue_count"])
    print("interaction  :", json.dumps(state.get("interaction"), ensure_ascii=False)[:400])
    print("transcript   :", len(state["items"]), "items on server")
    print("log          :", len(records), "records",
          "| agent turns:", len([r for r in records if r["role"] == "agent"]))
    if records:
        print("last ling    :", records[-1]["text"][:300].replace("\n", " "))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_send = sub.add_parser("send")
    p_send.add_argument("text")
    p_send.add_argument("--tag", default="")
    p_send.add_argument("--timeout", type=float, default=300.0)
    sub.add_parser("status")
    sub.add_parser("collect")
    p_appr = sub.add_parser("approve")
    p_appr.add_argument("token")
    p_appr.add_argument("--allow", action="store_true")
    p_appr.add_argument("--deny", action="store_true")
    p_ans = sub.add_parser("answer")
    p_ans.add_argument("token")
    p_ans.add_argument("text")
    sub.add_parser("export")
    args = parser.parse_args()

    if args.command == "send":
        send(args.text, args.tag, args.timeout)
    elif args.command == "status":
        status()
    elif args.command == "collect":
        collect()
    elif args.command == "approve":
        print(call("/api/approval", {"token": args.token, "allowed": bool(args.allow)}))
    elif args.command == "answer":
        print(call("/api/answer", {"token": args.token, "text": args.text}))
    elif args.command == "export":
        print("use export.py")


if __name__ == "__main__":
    main()

"""Export the Ling session that lives inside Nexus.

The canonical record is `.nexus-data/sessions/<id>.jsonl` — the file Nexus itself
writes. This script only reads it; the exports under `transcript/` and `viewer.html`
are derived snapshots of that session, not a second source of truth.

    python "docs/实验/ai-dialogue/tools/export.py"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parents[1]

ME = "外部 AI（DeepSeek-V4.1-Flash）"
LING = "灵（Nexus / DeepSeek-V4.1-Flash）"
BANNER = ("本文件是 AI 与 AI 之间的真实对话记录，属于合成测试数据。"
          "双方均为语言模型，没有人类参与者。对话内容未经过人工改写或补写。")


def ling_session_id(home: Path) -> str:
    connection = sqlite3.connect(home / "tasks.sqlite3")
    try:
        row = connection.execute("SELECT session_id FROM ling_identity WHERE id=1").fetchone()
    finally:
        connection.close()
    if not row:
        raise SystemExit("no ling_identity in tasks.sqlite3 — has Ling ever been started here?")
    return str(row[0])


def inbox_status(home: Path) -> dict[int, tuple[str, str]]:
    connection = sqlite3.connect(home / "tasks.sqlite3")
    try:
        rows = connection.execute("SELECT id, status, error FROM ling_inbox").fetchall()
    finally:
        connection.close()
    return {int(r[0]): (str(r[1]), str(r[2] or "")) for r in rows}


def load_session(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_turns(records: list[dict], status: dict[int, tuple[str, str]]) -> list[dict]:
    turns: list[dict] = []
    current: dict | None = None
    for record in records:
        kind = record.get("kind")
        if kind == "user":
            input_id = record.get("ling_input_id")
            state, error = status.get(int(input_id), ("", "")) if input_id else ("", "")
            current = {"turn": len(turns) + 1, "at": record.get("at", ""),
                       "agent": record.get("text", ""), "ling": "", "thinking": "",
                       "tools": [], "tool_results": 0,
                       "status": "failed" if state == "failed" else "", "error": error}
            turns.append(current)
        elif kind == "assistant" and current is not None:
            if record.get("text"):
                current["ling"] += (("\n\n" if current["ling"] else "") + record["text"])
            if record.get("thinking"):
                current["thinking"] += (("\n\n" if current["thinking"] else "") + record["thinking"])
            for call in record.get("tool_calls") or []:
                name = call.get("name") if isinstance(call, dict) else None
                if name:
                    current["tools"].append(name)
        elif kind == "tool" and current is not None:
            current["tool_results"] += 1
    return turns


def previous_tags(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    tags: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("role") == "agent" and record.get("tag"):
            tags[int(record["turn"])] = record["tag"]
    return tags


def flatten(turns: list[dict], tags: dict[int, str]) -> list[dict]:
    out: list[dict] = []
    for turn in turns:
        tag = tags.get(turn["turn"], "")
        out.append({"turn": turn["turn"], "role": "agent", "speaker": ME,
                    "text": turn["agent"], "at": turn["at"], "tag": tag})
        record = {"turn": turn["turn"], "role": "ling", "speaker": LING,
                  "text": turn["ling"], "thinking": turn["thinking"],
                  "tools": turn["tools"], "tool_results": turn["tool_results"],
                  "at": turn["at"], "tag": tag}
        if turn["status"]:
            record["status"] = turn["status"]
            record["error"] = turn["error"]
        out.append(record)
    return out


def stats(records: list[dict]) -> dict:
    turns = len([r for r in records if r["role"] == "agent"])
    return {
        "turns": turns,
        "records": len(records),
        "agent_chars": sum(len(r["text"]) for r in records if r["role"] == "agent"),
        "ling_chars": sum(len(r["text"]) for r in records if r["role"] == "ling"),
        "thinking_chars": sum(len(r.get("thinking") or "") for r in records),
        "tool_calls": sum(len(r.get("tools") or []) for r in records),
        "failed": [r["turn"] for r in records if r.get("status") == "failed"],
    }


def markdown(records: list[dict], info: dict) -> str:
    s = stats(records)
    out = [
        "# 灵 × 外部 AI：对话记录（Nexus 会话快照）",
        "",
        f"> {BANNER}",
        "",
        f"- 正本：Nexus 会话 `{info['session']}`（`{info['session_path']}`）",
        f"- 轮次：{s['turns']} 轮；外部 AI 约 {s['agent_chars']} 字，灵约 {s['ling_chars']} 字",
        f"- 工具调用 {s['tool_calls']} 次；内部思考 {s['thinking_chars']} 字",
        f"- 导出时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        "本文件是正本的派生快照。正本由 Nexus 自己写入，包含完整的工具调用与思考过程；"
        "两者不一致时，以正本为准。",
        "",
        "---",
        "",
    ]
    for record in records:
        who = ME if record["role"] == "agent" else LING
        tag = f"（{record['tag']}）" if record.get("tag") else ""
        out.append(f"## 第 {record['turn']} 轮 · {who}{tag}")
        out.append("")
        if record.get("status") == "failed":
            out.append(f"*[本轮失败：{record.get('error', '')}。无回复文本，未补写。]*")
        else:
            out.append(record["text"])
        if record["role"] == "ling":
            if record.get("tools"):
                out.append("")
                out.append(f"`工具：{' · '.join(record['tools'])}`")
            if record.get("thinking"):
                out.append("")
                out.append("<details><summary>思考过程</summary>")
                out.append("")
                out.append(record["thinking"])
                out.append("")
                out.append("</details>")
        out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out)


def html(records: list[dict], info: dict) -> str:
    s = stats(records)
    payload = json.dumps(records, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>灵 × 外部 AI · 对话记录</title>
<style>
  :root {{
    --bg:#f7f7f5; --panel:#fff; --ink:#1d1d1f; --muted:#6b6b70; --line:#e4e4e1;
    --agent:#f1f0ec; --ling:#eef3fb; --accent:#3b5bdb; --warn:#b4552d;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.75 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
  header{{position:sticky;top:0;z-index:10;background:rgba(247,247,245,.94);
    backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:16px 24px 12px}}
  h1{{margin:0 0 6px;font-size:17px;font-weight:600;letter-spacing:.2px}}
  .banner{{display:inline-block;font-size:12px;color:var(--warn);border:1px solid #e8c9ba;
    background:#fdf3ee;border-radius:999px;padding:2px 10px;margin-bottom:8px}}
  .stats{{font-size:12.5px;color:var(--muted)}}
  .stats span{{margin-right:14px}}
  .tools{{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}}
  input[type=search]{{flex:1;min-width:220px;padding:7px 12px;font-size:13.5px;
    border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);outline:none}}
  input[type=search]:focus{{border-color:var(--accent)}}
  button{{padding:7px 12px;font-size:13px;border:1px solid var(--line);border-radius:8px;
    background:#fff;color:var(--ink);cursor:pointer}}
  button:hover{{border-color:var(--accent);color:var(--accent)}}
  main{{max-width:860px;margin:0 auto;padding:28px 24px 120px}}
  .turn{{margin-bottom:22px}}
  .head{{display:flex;align-items:baseline;gap:10px;margin-bottom:6px;flex-wrap:wrap}}
  .who{{font-size:12.5px;font-weight:600;letter-spacing:.3px}}
  .agent .who{{color:#55555a}} .ling .who{{color:var(--accent)}}
  .n{{font-size:11.5px;color:#a0a0a5;font-variant-numeric:tabular-nums}}
  .tag{{font-size:11.5px;color:var(--muted);background:#ececea;border-radius:4px;padding:1px 6px}}
  .bubble{{border-radius:10px;padding:14px 18px;white-space:pre-wrap;word-break:break-word;
    border:1px solid var(--line)}}
  .agent .bubble{{background:var(--agent)}} .ling .bubble{{background:var(--ling)}}
  .failed .bubble{{background:#fdf3ee;border-color:#e8c9ba;color:var(--warn);font-style:italic}}
  details{{margin-top:8px;font-size:13px;color:var(--muted)}}
  summary{{cursor:pointer;user-select:none}}
  details .inner{{white-space:pre-wrap;border-left:2px solid var(--line);padding:6px 0 2px 12px;margin-top:6px}}
  .chips{{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}}
  .chip{{font-size:11.5px;color:#4a4a50;background:#e9e9e6;border-radius:999px;padding:2px 9px;
    font-family:ui-monospace,Consolas,monospace}}
  mark{{background:#ffe9a8;color:inherit;padding:0 1px}}
  footer{{max-width:860px;margin:0 auto;padding:0 24px 60px;font-size:12.5px;color:var(--muted)}}
  code{{background:#ececea;padding:1px 5px;border-radius:4px;font-size:12.5px}}
</style>
</head>
<body>
<header>
  <div class="banner">AI 间合成测试数据 · 无人类参与者</div>
  <h1>灵 × 外部 AI（DeepSeek-V4.1-Flash）</h1>
  <div class="stats">
    <span>{s['turns']} 轮</span><span>灵约 {s['ling_chars']} 字</span>
    <span>工具调用 {s['tool_calls']} 次</span><span>会话 {info['session']}</span>
  </div>
  <div class="tools">
    <input id="q" type="search" placeholder="搜索对话内容…">
    <button id="prev">上一轮</button><button id="next">下一轮</button><button id="top">回到开头</button>
  </div>
</header>
<main id="list"></main>
<footer>
  <p>正本为 Nexus 会话 <code>{info['session']}</code>，由 Nexus 自己写入；本页是它的派生快照，
     含完整的内部思考与工具调用记录。第 28 轮灵一侧模型连接中断，如实保留为失败条目。</p>
  <p>人设卡：<code>persona/persona.json</code> · 派生数据：<code>transcript/dialogue.jsonl</code> · 说明：<code>README.md</code></p>
</footer>
<script>
const DATA = {payload};
const list = document.getElementById('list');
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
function render(filter) {{
  list.innerHTML = '';
  const f = (filter || '').trim();
  DATA.forEach(r => {{
    const failed = r.status === 'failed';
    const body = failed ? ('[本轮失败] ' + (r.error || '')) : r.text;
    const hay = body + (r.thinking || '') + (r.tag || '');
    if (f && !hay.includes(f)) return;
    const el = document.createElement('div');
    el.className = 'turn ' + r.role + (failed ? ' failed' : '');
    el.id = 't' + r.turn + '-' + r.role;
    let shown = esc(body);
    if (f) shown = shown.split(esc(f)).join('<mark>' + esc(f) + '</mark>');
    let extra = '';
    if (r.tools && r.tools.length) {{
      extra += '<div class="chips">' + r.tools.map(t => '<span class="chip">' + esc(t) + '</span>').join('') + '</div>';
    }}
    if (r.thinking) {{
      extra += '<details><summary>思考过程（' + r.thinking.length + ' 字）</summary>'
             + '<div class="inner">' + esc(r.thinking) + '</div></details>';
    }}
    el.innerHTML = '<div class="head"><span class="n">#' + r.turn + '</span>'
      + '<span class="who">' + (r.role === 'agent' ? '外部 AI' : '灵') + '</span>'
      + (r.tag ? '<span class="tag">' + esc(r.tag) + '</span>' : '') + '</div>'
      + '<div class="bubble">' + shown + '</div>' + extra;
    list.appendChild(el);
  }});
}}
document.getElementById('q').addEventListener('input', e => render(e.target.value));
document.getElementById('top').onclick = () => window.scrollTo({{top:0,behavior:'smooth'}});
let cursor = 0;
function jump(d) {{
  const nodes = [...document.querySelectorAll('.turn')];
  if (!nodes.length) return;
  cursor = Math.max(0, Math.min(nodes.length - 1, cursor + d));
  nodes[cursor].scrollIntoView({{behavior:'smooth',block:'center'}});
}}
document.getElementById('prev').onclick = () => jump(-1);
document.getElementById('next').onclick = () => jump(1);
render('');
</script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=str(ROOT / ".nexus-data"))
    parser.add_argument("--session", default="")
    args = parser.parse_args()

    home = Path(args.home)
    session = args.session or ling_session_id(home)
    path = home / "sessions" / f"{session}.jsonl"
    if not path.exists():
        raise SystemExit(f"session file not found: {path}")

    records = load_session(path)
    turns = build_turns(records, inbox_status(home))
    tags = previous_tags(OUT / "transcript" / "dialogue.jsonl")
    flat = flatten(turns, tags)
    info = {"session": session, "session_path": str(path.relative_to(ROOT))}

    (OUT / "transcript" / "dialogue.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in flat) + "\n", encoding="utf-8")
    (OUT / "transcript" / "dialogue.md").write_text(markdown(flat, info), encoding="utf-8")
    (OUT / "viewer.html").write_text(html(flat, info), encoding="utf-8")

    s = stats(flat)
    print(f"session {session}: {s['turns']} turns, {s['records']} records, "
          f"{s['tool_calls']} tool calls, failed={s['failed']}")


if __name__ == "__main__":
    main()

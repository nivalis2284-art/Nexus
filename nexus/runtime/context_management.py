"""Read-only context snapshot and owner/role persistence."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .. import skills
from ..core import prompt
from ..core.permission import default_policy
from ..core.workspace import inspect
from ..instructions import OWNER_ITEMS_NAME, load_instructions, save_instructions
from ..role_instructions import ROLE_IDS, load_role, save_role
from ..session.presentation import transcript
from ..tools import build
from .history import effective_checkpoint, history_version

ROLE_TITLES = {"main": "主交流", "work": "工作", "subagent": "子代理"}
SECTION_TITLES = {"environment": "运行环境", "priority": "指令来源与优先级",
                  "project": "项目指令", "skills": "可用技能", "interaction": "当前交流与工作状态",
                  "task": "工作状态", "permission": "有效权限", "tools": "本轮可用工具"}


def _owner_items(home) -> list[str]:
    """Structured items, or one legacy item when only the text file exists."""
    path = Path(home).expanduser() / OWNER_ITEMS_NAME
    text = load_instructions(home)
    if not path.is_file():
        return [text] if text else []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"owner-instruction-items.json is not valid JSON: {error}") from error
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("owner instruction items must be a list of text items")
    return value if "\n\n".join(value) == text else ([text] if text else [])


def save_owner_items(home, items) -> list[str]:
    """Persist order and boundaries; the compatibility text always equals their join."""
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ValueError("owner instruction items must be a list of text items")
    if any(not item.strip() for item in items):
        raise ValueError("owner instruction items must not be empty")
    path = Path(home).expanduser() / OWNER_ITEMS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "\n\n".join(items)
    save_instructions(home, rendered)
    from ..file_io import replace_text
    replace_text(path, json.dumps(items, ensure_ascii=False, indent=2) + "\n")
    return items


def _main_sections(state, workspace, role_text: str, owner_text: str) -> list[dict[str, str]]:
    settings = state["store"].load()
    skill_set = skills.discover([
        *skills.default_roots(workspace.root, state["store"].home),
        Path(__file__).parents[1] / "builtin_skills",
    ])
    tool_config = {
        "webfetch_hosts": settings.webfetch_hosts,
        "search_endpoint": settings.search_endpoint,
    }
    from ..ling import WorkService
    from ..attachments import AttachmentStore
    interaction = state["ling"]
    session = state["sessions"].load(interaction.session_id)
    work = WorkService(state, session, settings, interaction.current_input)
    registry, _ = build(
        skill_set.bodies(), root=workspace.root, config=tool_config,
        mcp=state.get("mcp"), attachment_source=(AttachmentStore(state["store"].home),
                                                "sessions", interaction.session_id),
        work_service=work, owner_home=state["store"].home, owner_writable=True,
    )
    from ..core.agent import _platform
    return [section for section in prompt.build_system_sections(
        workspace,
        registry.declarations(),
        platform=_platform(),
        date=time.strftime("%Y-%m-%d"),
        role=role_text,
        owner_instructions=owner_text,
        skills=skill_set.manifest(),
        task_context="",
        interaction_context=work.context_text(),
        permission_context=prompt.permission_text(default_policy(settings.permission,
                                                                 settings.permission_rules)),
    ) if section["id"] not in ("owner", "role")]


def snapshot(state, session) -> dict[str, Any]:
    """The main conversation's current assembled sources and effective history."""
    home = state["store"].home
    owner_items = _owner_items(home)
    owner_text = load_instructions(home)
    roles = [{"id": role_id, "title": ROLE_TITLES[role_id], "text": load_role(home, role_id)}
             for role_id in ROLE_IDS]
    settings = state["store"].load()
    workspace = inspect(Path(settings.workspace))
    sections = _main_sections(state, workspace, roles[0]["text"], owner_text)
    for section in sections:
        section["title"] = SECTION_TITLES[section["id"]]

    checkpoint = effective_checkpoint(session)
    summary = None
    covered = -1
    if checkpoint is not None:
        index, item = checkpoint
        text = item.text.removeprefix(prompt.compaction.SUMMARY_TAG_OPEN).removesuffix(
            prompt.compaction.SUMMARY_TAG_CLOSE).strip()
        summary = {"index": index, "text": text,
                   "through": str(item.data.get("through", ""))}
        covered = int(item.data.get("through", -1))
    messages = [message for message in transcript(session)
                if isinstance(message.get("index"), int) and message["index"] > covered]
    return {
        "owner_instructions": owner_items,
        "roles": roles,
        "sections": sections,
        "summary": summary,
        "messages": messages,
        "version": history_version(session),
    }

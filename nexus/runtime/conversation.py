"""Main conversation execution, independent of HTTP connections."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from .. import skills
from ..attachments import AttachmentStore, message_content
from ..core.agent import Agent, AgentSuspended
from ..core.config import build_profile
from ..core.permission import default_policy
from ..core.workspace import inspect
from ..instructions import load_instructions
from ..role_instructions import load_role
from ..model import RuntimeCredentials
from ..session import Message
from ..task import TaskConflict
from ..tools import build
from ..tools._jobs import JobRegistry
from ..tools.registry import TaskSuspension, ToolResult
from .controller import needs_project_write


def persist(state, session, new_messages):
    store = state["sessions"]
    for message in new_messages:
        role = message.get("role")
        if role == "assistant":
            if not (message.get("content") or message.get("tool_calls") or message.get("thinking") or message.get("native")):
                continue
            data = {
                key: message[key]
                for key in ("message_id", "tool_calls", "thinking", "usage", "elapsed", "native")
                if message.get(key) is not None
            }
            store.append(
                session, Message(kind="assistant", text=message.get("content") or "", data=data)
            )
        elif role == "tool":
            data = {
                key: message.get(key)
                for key in ("tool_call_id", "parent_message_id", "name", "arguments", "error", "ok", "details")
            }
            store.append(
                session, Message(kind="tool", text=message.get("content") or "", data=data)
            )
        elif role == "checkpoint":
            store.append(session, Message(kind="checkpoint", text=message["content"],
                                          data={"through": message["through"]}))
        else:
            continue
        message["_source_id"] = str(len(session.items) - 1)
        if state.get("plugins"):
            state["plugins"].publish(
                "message.saved",
                {
                    "session_id": session.id,
                    "kind": session.items[-1].kind,
                    "text": session.items[-1].text,
                    "data": session.items[-1].data,
                },
            )


def history_for_model(session) -> list[dict[str, Any]]:
    """Rebuild the provider-neutral history the agent consumes.

    Native continuation remains separate from display-only thinking text.
    """
    out: list[dict[str, Any]] = []
    for index, item in enumerate(session.items):
        before = len(out)
        if item.kind == "user":
            content = message_content(item.text, item.data.get("attachments") or [], include_images=True)
            if item.data.get("images"):
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}] if content else []
                content.extend({"type": "image", **image} for image in item.data["images"])
            if item.data.get("task_id"):
                prefix = "[This message explicitly targets work " + item.data["task_id"] + ". Information alone does not resume it.]\n"
                if isinstance(content, str):
                    content = prefix + content
                else:
                    content.insert(0, {"type": "text", "text": prefix})
            out.append({"role": "user", "content": content})
        elif item.kind == "assistant":
            calls = item.data.get("tool_calls") or []
            if item.text or calls or item.data.get("native"):
                out.append({"role": "assistant", "content": item.text, "tool_calls": calls,
                            **({"native": item.data["native"]} if item.data.get("native") else {})})
        elif item.kind == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": item.data.get("tool_call_id", ""),
                    "name": item.data.get("name", ""),
                    "content": item.text,
                }
            )
        elif item.kind == "checkpoint":
            out.append({"role": "checkpoint", "content": item.text,
                        "through": item.data.get("through")})
        elif item.kind in ("experience", "work_report"):
            out.append({"role": "user", "content": "[Internal experience, not a new user instruction] " + item.text})
        for message in out[before:]:
            message["_source"] = {"session_id": session.id, "index": index}
            message["_source_id"] = str(index)
    from ..core.compaction import project
    return project(out)


def run_turn(state, session, settings, emit, *, ling, history):
    from ..ling import WorkService

    workspace = inspect(Path(settings.workspace))
    skill_set = skills.discover(
        [
            *skills.default_roots(workspace.root, state["store"].home),
            Path(__file__).parents[1] / "builtin_skills",
        ]
    )
    state["skills"] = skill_set
    session_jobs = state.setdefault("jobs", {}).setdefault(session.id, JobRegistry())
    if session_jobs.closed:
        session_jobs = state["jobs"][session.id] = JobRegistry()
    cancel = threading.Event()
    with ling._condition:
        ling.cancel_event = cancel
        if ling.stop_through_input and ling.current_input <= ling.stop_through_input:
            cancel.set()
    writer = f"main:{session.id}:{ling.current_input}"
    work = WorkService(state, session, settings, ling.current_input)
    decisions = ling.decisions
    plugins = state.get("plugins")
    policy = default_policy(settings.permission, settings.permission_rules)
    scope = {
        "kind": "ling",
        "owner_id": session.id,
        "session_id": session.id,
        "input_id": ling.current_input,
    }
    tool_config = {
        "webfetch_hosts": settings.webfetch_hosts,
        "search_endpoint": settings.search_endpoint,
    }

    def toolset(supervise=True):
        return build(
            skill_set.bodies(),
            root=workspace.root,
            config=tool_config,
            mcp=state.get("mcp"),
            jobs=session_jobs,
            cancel_event=cancel,
            attachment_source=(AttachmentStore(state["store"].home), "sessions", session.id),
            attachment_ids={
                a["id"] for item in session.items for a in item.data.get("attachments", [])
            },
            work_service=work if supervise else None,
            owner_home=state["store"].home,
            owner_writable=supervise,
        )

    registry, context = toolset()
    saved = 0

    def checkpoint(message):
        nonlocal saved
        if message.get("role") == "assistant" and not message.get("tool_calls"):
            message["elapsed"] = round(time.monotonic() - started, 3)
        persist(state, session, [message])
        saved += 1
        with ling._condition:
            ling.live = {}
        from .stream import runtime_updates
        runtime_updates(state).refresh("ling", session.id, clear=True)

    def ask(questions):
        answer = decisions.consume_question(questions)
        if answer is not None:
            return answer
        request = decisions.create("question", {"questions": questions})
        emit({"type": "question", "token": request["id"], "questions": questions})
        raise TaskSuspension("等待你回答；可以继续其他交流", kind="question")

    async def approve(request):
        if ling.current_report:
            return False
        # Persist validated defaults too, so the single-use grant matches actual dispatch.
        prepared = registry.prepare(request.tool, request.arguments, context)
        arguments = prepared.arguments if hasattr(prepared, "arguments") else request.arguments
        if decisions.granted(request.tool, arguments, str(workspace.root)):
            return True
        pending = decisions.create(
            "approval", {"tool": request.tool, "arguments": arguments, "value": request.value,
                         "workspace": str(workspace.root)}
        )
        emit({"type": "approval", "token": pending["id"], **pending["payload"]})
        raise AgentSuspended("等待你批准；可以继续其他交流", kind="approval")

    def execute(prepared, tool_context=context, tools=registry):
        if ling.current_report and not prepared.spec.read_only:
            return ToolResult(
                False,
                "A work event asks only for a contextual report. It does not authorize starting work, editing instructions or other new actions.",
                error_code="report_only",
            )
        if needs_project_write(prepared.spec):
            try:
                state["task_controller"].acquire_write(
                    writer, str(workspace.root), cancel, wait=False
                )
            except TaskConflict:
                return ToolResult(
                    False,
                    "Project is being modified by another work. This call was not executed. Delegate the operation with work_start so it can queue; keep the main conversation available.",
                    error_code="project_busy",
                )
        request_id = decisions.dispatch(prepared.spec.name, prepared.arguments, str(workspace.root))
        try:
            result = tools.execute_prepared(prepared, tool_context)
        except AgentSuspended as suspension:
            decisions.finish(request_id, unknown=suspension.control != "children")
            raise
        except BaseException:
            decisions.finish(request_id, unknown=True)
            raise
        decisions.finish(request_id)
        return result

    context.ask = ask
    profile = build_profile(settings.provider, settings.model)

    def before_model(messages):
        ling.apply_pending(session, messages)
        work.input_id = ling.current_input
        context.attachment_ids = {
            a["id"] for item in session.items for a in item.data.get("attachments", [])
        }
        selected = state["store"].load()
        agent.profile = build_profile(selected.provider, selected.model)
        agent.credentials = RuntimeCredentials(selected.api_key)
        agent.effort = selected.effort
        agent.owner_context = load_instructions(state["store"].home)
        agent.role = load_role(state["store"].home, "main")
        agent.interaction_context = work.context_text()

    agent = Agent(
        profile=profile,
        credentials=RuntimeCredentials(settings.api_key),
        registry=registry,
        context=context,
        workspace=workspace,
        policy=policy,
        max_steps=settings.max_steps,
        context_limit=settings.context_limit,
        effort=settings.effort,
        skills=skill_set.manifest(),
        approve=approve,
        cancel_event=cancel,
        on_message=checkpoint,
        before_model=before_model,
        execute_prepared=execute,
        interaction_context=work.context_text(),
        plugins=plugins,
        plugin_scope=scope,
        request_history=state.get("request_history"),
        request_scope={"kind": "ling", "owner_id": session.id},
        role=load_role(state["store"].home, "main"),
    )
    ling.agent = agent

    from .subagents import SubagentService
    context.subagents = SubagentService(state["tasks"], state["task_controller"], state["store"],
        session.id, "session", settings=settings, history=lambda: history,
        call_id=lambda: getattr(context, "call_id", str(ling.current_input)),
        attachments=lambda: [a for item in session.items for a in item.data.get("attachments", [])])
    started = time.monotonic()

    async def drive():
        async for event in agent.run(history):
            if event.type in ("delta", "thinking"):
                emit({"type": event.type, "text": event.text,
                      "message_id": (event.data or {}).get("message_id")})
            elif event.type in ("tool_start", "tool_result"):
                emit({"type": event.type, "tool": event.data})
            elif event.type == "error":
                emit({"type": "error", "message": event.text})
            elif event.type == "done":
                emit(
                    {
                        "type": "done",
                        "data": {**event.data, "elapsed": round(time.monotonic() - started, 3)},
                    }
                )
            else:
                emit({"type": event.type, "data": event.data})

    try:
        asyncio.run(drive())
    except (AgentSuspended, TaskSuspension):
        ling.result_status = "waiting_user"
    finally:
        agent.close_pending_calls(history)
        persist(state, session, agent.new_messages[saved:])
        from .stream import runtime_updates
        runtime_updates(state).refresh("ling", session.id, clear=True)
        # A detached command retains the project writer until its process exits.
        state["task_controller"].release_when_jobs_finish(writer, session_jobs)
        ling.agent = None
        ling.cancel_event = None

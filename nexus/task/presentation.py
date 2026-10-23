"""Readable, display-only projections of persistent task facts.

The task state machine remains authoritative.  This module only combines the
existing state, reason, schedule, interaction, and progress records into the
few labels shared by the task manager and Ling's work summaries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


STATE_LABELS = {
    "pending": "待执行",
    "queued": "排队中",
    "running": "执行中",
    "pausing": "正在挂起",
    "paused": "已挂起",
    "waiting_user": "等待你处理",
    "completed": "已完成",
    "cancelled": "已取消",
}

_STATE_DETAILS = {
    "pending": "尚未开始；点击开始后进入执行队列。",
    "queued": "已加入执行队列，等待当前工作结束。",
    "running": "正在执行当前步骤。",
    "pausing": "已请求挂起，正在停止当前执行。",
    "paused": "已挂起；不会因补充信息而自动继续。",
    "waiting_user": "等待你处理；完成确认后继续保存的工作。",
    "completed": "任务已完成。",
    "cancelled": "任务已取消；重新启用后才能再次开始。",
}


def _field(task: Any, name: str, default: Any = "") -> Any:
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def _pending_label(interactions: list[dict[str, Any]]) -> str:
    kinds = {str(item.get("kind") or "") for item in interactions}
    if "approval" in kinds:
        return "等待你批准操作"
    if "question" in kinds:
        return "等待你回答问题"
    if "start_confirmation" in kinds:
        return "等待你确认开始"
    return "等待你处理"


def _schedule_label(store: Any, task: Any, interactions: list[dict[str, Any]]) -> str:
    schedule = store.schedule(_field(task, "id"))
    if not schedule:
        return "未安排定时。"

    due_at = str(schedule.get("due_at") or "").strip()
    timezone_name = str(schedule.get("timezone") or "UTC").strip() or "UTC"
    mode = "到时自动继续" if schedule.get("mode") == "auto" else "到时等待确认"
    try:
        due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        # due_at is stored as an absolute UTC instant.  set_schedule keeps the
        # supplied offset for fixed-offset labels; IANA names resolve here.
        fixed_offset = timezone_name.upper().startswith(
            ("UTC+", "UTC-", "GMT+", "GMT-")
        )
        if not fixed_offset:
            due = due.astimezone(ZoneInfo(timezone_name))
        shown = due.strftime("%Y-%m-%d %H:%M")
        overdue = due < datetime.now(timezone.utc)
    except (TypeError, ValueError):
        shown = due_at or "时间无效"
        overdue = False

    stamp = f"{shown}（{timezone_name}）"
    status = str(schedule.get("status") or "pending")
    if status == "consumed" and any(
        item.get("kind") == "start_confirmation" for item in interactions
    ):
        return f"{stamp} · 等待确认"
    if status == "claimed":
        return f"{stamp} · 已触发，等待处理"
    if status == "consumed":
        return f"{stamp} · 已执行"
    if overdue:
        return f"{stamp} · 已到期，等待调度 · {mode}"
    return f"{stamp} · {mode}"


def _status_detail(store: Any, controller: Any, task: Any,
                   interactions: list[dict[str, Any]]) -> str:
    task_id = _field(task, "id")
    state = _field(task, "state")
    parts = [_STATE_DETAILS.get(state, str(state))]

    if _field(task, "archived", False):
        parts.append("已归档。")

    if state == "waiting_user":
        parts[0] = _pending_label(interactions) if interactions else "等待你处理。"

    active = getattr(controller, "active_task", None) == task_id
    queued_ids = controller.queued_ids()
    if active and state == "running":
        parts.append("正在使用当前执行通道。")
    elif task_id in queued_ids and state == "queued":
        parts.append("前面还有其他排队工作。")

    wait_reason = str(_field(task, "wait_reason") or "").strip()
    if wait_reason:
        if state == "waiting_user" and interactions:
            # The interaction kind already supplies the actionable label; the
            # persisted reason remains available in the raw task response.
            pass
        else:
            parts.append(wait_reason)

    blocked = str(_field(task, "blocked") or "").strip()
    if blocked:
        parts.append("受阻：" + blocked)

    next_step = str(_field(task, "next_step") or "").strip()
    if next_step and state != "completed":
        parts.append("下一步：" + next_step)

    if interactions:
        parts.append(f"有 {len(interactions)} 项待你处理。")

    input_status = store.input_status(task_id)
    pending_inputs = int((input_status or {}).get("pending_count", 0) or 0)
    if pending_inputs:
        parts.append(f"有 {pending_inputs} 条补充信息待送入。")

    unfinished = store.unfinished_operations(task_id)
    if unfinished:
        parts.append(f"有 {len(unfinished)} 项操作结果待确认。")
    return " ".join(parts)


def _progress_text(task: Any) -> str:
    summary = str(_field(task, "work_summary") or "").strip()
    return summary or "暂无进展说明"


def task_presentation(store: Any, controller: Any, task: Any) -> dict[str, str]:
    """Return display labels without changing or extending task state."""
    state = str(_field(task, "state") or "")
    interactions = store.pending_interactions(_field(task, "id"))
    state_label = STATE_LABELS.get(state, state)
    if state == "waiting_user":
        state_label = _pending_label(interactions) if interactions else "等待你处理"
    return {
        "state_label": state_label,
        "status_detail": _status_detail(store, controller, task, interactions),
        "schedule_label": _schedule_label(store, task, interactions),
        "progress_text": _progress_text(task),
    }


__all__ = ["STATE_LABELS", "task_presentation"]

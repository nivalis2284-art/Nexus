"""Explicit owner edits of the main conversation, outside a running turn."""

from copy import deepcopy

from ..core.compaction import SUMMARY_TAG_CLOSE, SUMMARY_TAG_OPEN
from ..task import TaskConflict, TaskValidationError
from .conversation import history_for_model


def history_version(session):
    stat = session.path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def effective_checkpoint(session):
    for message in reversed(history_for_model(session)):
        if message.get("through") is not None:
            index = int(message["_source_id"])
            return index, session.items[index]
    return None


def check_version(session, version):
    if version != history_version(session):
        raise TaskConflict("会话记录已更新，请刷新后再修改。")


def edit_history(session, body):
    """Change records as one rewrite; never replay the tools in old history."""
    check_version(session, body.get("version"))
    operation = body.get("operation")
    if operation == "summary":
        checkpoint = effective_checkpoint(session)
        if checkpoint is None or body.get("index") != checkpoint[0]:
            raise TaskConflict("摘要已更新，请刷新后重试。")
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise TaskValidationError("摘要不能为空。")
        checkpoint[1].text = (SUMMARY_TAG_OPEN + "\n" + text.strip() + "\n" + SUMMARY_TAG_CLOSE
                              if not text.strip().startswith(SUMMARY_TAG_OPEN) else text)
        return None
    indices = body.get("indices")
    if (not isinstance(indices, list) or not indices or
            any(type(index) is not int or index < 0 or index >= len(session.items) for index in indices)):
        raise TaskValidationError("请选择有效的消息。")
    checkpoint = effective_checkpoint(session)
    through = int(checkpoint[1].data["through"]) if checkpoint else -1
    if min(indices) <= through:
        raise TaskValidationError("这条消息已压缩，请在上下文管理中修改历史摘要。")
    if any(session.items[index].kind not in ("user", "assistant", "tool", "notice",
                                             "work_report", "experience") for index in indices):
        raise TaskValidationError("该记录不能作为会话消息修改。")
    resend = None
    if operation == "resend":
        index = indices[0]
        text = body.get("text")
        if len(indices) != 1 or session.items[index].kind != "user" or not isinstance(text, str) or not text.strip():
            raise TaskValidationError("只能编辑并重新发送非空的用户消息。")
        original = session.items[index]
        resend = {"text": text.strip(), "images": deepcopy(original.data.get("images") or []),
                  "attachments": [item["id"] for item in original.data.get("attachments") or []],
                  "task_id": original.data.get("task_id"), "request_id": body.get("request_id")}
        removed = set(range(index, len(session.items)))
        if checkpoint:
            removed.discard(checkpoint[0])
    elif operation == "delete":
        removed = set(indices)
        # Providers require calls and results to remain a complete exchange.
        call_ids = {call.get("id") for index in removed for call in session.items[index].data.get("tool_calls", [])}
        call_ids.update(session.items[index].data.get("tool_call_id") for index in removed if session.items[index].kind == "tool")
        call_ids.discard(None)
        for index, item in enumerate(session.items):
            calls = {call.get("id") for call in item.data.get("tool_calls", [])}
            if calls & call_ids:
                removed.add(index)
                call_ids.update(calls)
        removed.update(index for index, item in enumerate(session.items)
                       if item.kind == "tool" and item.data.get("tool_call_id") in call_ids)
    else:
        raise TaskValidationError("未知消息操作。")
    session.remove_items(removed)
    return resend

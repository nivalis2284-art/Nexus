"""Task input admission shared by Ling and the task page, without HTTP callbacks."""

from __future__ import annotations

from typing import Any

from ..attachments import IMAGE_MIMES, AttachmentStore
from ..core.config import ConfigStore
from ..task import TaskConflict, TaskStore, TaskValidationError
from .controller import TaskController


def accept_input(store: TaskStore, controller: TaskController, config: ConfigStore,
                 task_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Save once before execution; extra input never replaces a pending decision."""
    text = str(body.get("text") or "").strip()
    request_id = str(body.get("request_id") or "").strip()
    if (not text and not body.get("attachments")) or not request_id:
        raise TaskValidationError("text and request_id are required")
    task = store.get(task_id)
    previous = store.message_for_request(task_id, request_id)
    if previous is None and task.state in ("cancelled", "completed"):
        raise TaskConflict("re-enable or create a new task before adding work")
    attachments = AttachmentStore(config.home).resolve("tasks", task_id, body.get("attachments", []))
    if any(item["mime"] in IMAGE_MIMES for item in attachments):
        provider = next((item for item in config.providers() if item.id == task.provider_id), None)
        model = next((item for item in provider.models if item.get("id") == task.model_id), {}) if provider else {}
        if not model.get("capabilities", {}).get("images"):
            raise TaskValidationError("任务绑定的模型未声明图片能力，请更换模型或移除图片")
        if any(item["mime"] in IMAGE_MIMES and item["size"] > 5 * 1024 * 1024 for item in attachments):
            raise TaskValidationError("图片上限为 5 MiB")
    message = store.accept_user_message(
        task_id, text=text, request_id=request_id, data={"attachments": attachments},
    )
    return {"task_id": task_id, "text": text, "request_id": request_id,
            "seq": message["seq"], "delivery": "pending"}

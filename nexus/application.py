"""Application resources and operations, independent of the HTTP and page layers."""

from __future__ import annotations

import threading
from pathlib import Path

from . import skills
from .attachments import AttachmentStore
from .core.config import ConfigStore, build_profile
from .ling import LingInbox, ling_session_id
from .mcp import MCPError, MCPRegistry, ServerConfig
from .model.request_history import RequestHistory
from .plugins import PluginManager
from .runtime.assembly import TaskAssembler
from .runtime.connections import start_mcp
from .runtime.controller import TaskController
from .runtime.stream import runtime_updates
from .session import SessionStore
from .task import TaskConflict, TaskStore, TaskValidationError


MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES_PER_TURN = 8
ALLOWED_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


def read_images(raw):
    """Validate the existing image input shape before admitting a message."""
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("images must be a list")
    if len(raw) > MAX_IMAGES_PER_TURN:
        raise ValueError(f"too many images (max {MAX_IMAGES_PER_TURN})")
    out = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"image {index} is not an object")
        mime = str(item.get("mime") or "").strip().lower()
        data = item.get("data")
        if mime not in ALLOWED_IMAGE_MIMES:
            raise ValueError(f"image {index} has unsupported type '{mime}'")
        if not isinstance(data, str) or not data:
            raise ValueError(f"image {index} has no data")
        if len(data) * 3 // 4 > MAX_IMAGE_BYTES:
            raise ValueError(f"image {index} is larger than 5MB")
        out.append({"mime": mime, "data": data})
    return out


def load_skills(store, workspace):
    return skills.discover([*skills.default_roots(workspace, store.home),
                            Path(__file__).parent / "builtin_skills"])


class Application:
    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.busy = threading.Lock()
        self.resources = threading.Lock()
        self.state["application"] = self

    def start(self, *, home=None, workspace=None):
        """Create the existing runtime; the host may attach plugin HTTP afterwards."""
        state = self.state
        try:
            store = ConfigStore(home)
            store.ensure()
            if workspace is not None:
                store.save_preferences(workspace=str(Path(workspace).resolve()))
            state.update(store=store, sessions=SessionStore(store.sessions_dir), jobs={}, closing=False)
            root = Path(store.load().workspace)
            state["skills"] = load_skills(store, root)
            state["mcp"] = start_mcp(store.load().mcp_servers, root) or MCPRegistry()
            tasks = TaskStore(store.home / "tasks.sqlite3")
            tasks.recover_interrupted()
            tasks.hold_queued_at_startup()
            state["tasks"] = tasks
            state["request_history"] = RequestHistory(store.home / "model-requests.sqlite3")
            state["plugins"] = PluginManager(state)
            feed = runtime_updates(state)
            runner = TaskAssembler(tasks, store, mcp_registry=state["mcp"],
                plugins=state["plugins"], request_history=state["request_history"], updates=feed)
            controller = TaskController(tasks, run=runner,
                on_change=lambda task_id: feed.refresh("task", task_id))
            runner.controller = controller
            state.update(task_runner=runner, task_controller=controller)
            controller.start()
            self.inbox()
        except BaseException:
            self.close()
            raise
        return self

    def attach_http(self, base_url):
        """Preserve the plugin callback/page contract once the host binds its port."""
        self.state["plugins"].start(base_url)

    def inbox(self):
        with self.resources:
            if self.state.get("closing"):
                raise TaskConflict("服务正在关闭")
            if "tasks" not in self.state:
                raise TaskValidationError("task service is not initialized")
            if self.state.get("ling") is None:
                def run(session, emit, inbox):
                    self.run_turn(session, self.state["store"].load(), emit, ling=inbox)
                self.state["ling"] = LingInbox(self.state, run, self.busy)
            return self.state["ling"]

    def run_turn(self, session, settings, emit, *, ling=None):
        from .runtime.conversation import history_for_model, run_turn

        inbox = ling or self.inbox()
        with self.resources:
            if self.state.get("closing"):
                raise TaskConflict("服务正在关闭")
        return run_turn(self.state, session, settings, emit, ling=inbox,
                        history=history_for_model(session))

    def submit_input(self, body):
        inbox = self.inbox()
        text = str(body.get("text") or "").strip()
        images = read_images(body.get("images"))
        request_id = str(body.get("request_id") or "")
        if not request_id or len(request_id) > 200:
            raise TaskValidationError("request_id is required (at most 200 characters)")
        target = body.get("task_id")
        if target is not None and not isinstance(target, str):
            raise TaskValidationError("task_id must be text")
        if target:
            self.state["tasks"].get(target)
        words = text.lower().strip().rstrip("。.!！ ")
        if not images and not body.get("attachments") and words in ("停一下", "先停一下", "暂停", "停止", "stop", "stop now"):
            notice = "已请求挂起所选工作；其余工作继续。" if target else "已停止当前主交流；后台工作继续。"
            accepted = inbox.enqueue(text, [], [], request_id, target, control=notice)
            if not accepted.get("duplicate"):
                if target:
                    self.state["task_controller"].pause(target, request_id=request_id)
                else:
                    inbox.stop()
            return accepted
        if not images and not body.get("attachments") and words in ("暂停全部工作", "暂停所有工作", "全部工作暂停"):
            accepted = inbox.enqueue(text, [], [], request_id, control="已请求挂起全部工作。")
            if not accepted.get("duplicate"):
                self.state["task_controller"].pause_all()
            return accepted
        settings = self.state["store"].load()
        if not settings.api_key or not settings.provider.base_url:
            raise TaskValidationError("请先配置模型的 Base URL 和 API Key")
        build_profile(settings.provider, settings.model)
        if images and not settings.model.get("capabilities", {}).get("images"):
            raise TaskValidationError("当前模型没有声明图片能力")
        attachments = AttachmentStore(self.state["store"].home).resolve(
            "sessions", inbox.session_id, body.get("attachments", []))
        if len(images) + len(attachments) > 8:
            raise TaskValidationError("每次最多 8 个附件")
        return inbox.enqueue(text, images, attachments, request_id, target)

    def save_settings(self, value):
        from .runtime.settings import save_settings

        return save_settings(self.state, value, self.busy, self.workspace_changed)

    def workspace_changed(self, workspace):
        store, live = self.state["store"], self.state.get("mcp")
        if live:
            for entry in store.load().mcp_servers:
                config = ServerConfig.from_dict(entry)
                if config is not None:
                    live.remove(config.name)
                    if config.enabled:
                        try:
                            live.add(config, cwd=workspace)
                        except MCPError:
                            pass
        self.state["skills"] = load_skills(store, workspace)

    def select_model(self, value):
        from .core.effort import normalize

        provider_id = str(value.get("provider") or "")
        model_id = str(value.get("model") or "")
        if not provider_id or not model_id:
            raise ValueError("provider and model are required")
        level = value.get("effort")
        self.state["store"].select(provider_id, model_id, normalize(level) if level is not None else None)

    def delete_session(self, session_id):
        state = self.state
        if session_id == ling_session_id(state.get("tasks")):
            raise TaskConflict("灵的持续记录由统一入口管理，不能作为普通会话删除。")
        removed = state["sessions"].delete(session_id)
        if removed:
            AttachmentStore(state["store"].home).delete_owner("sessions", session_id)
            if state.get("request_history"):
                state["request_history"].delete_owner(session_id)
            jobs = state.get("jobs", {}).pop(session_id, None)
            if jobs is not None:
                jobs.close_all()
        return removed

    def close(self):
        state = self.state
        with self.resources:
            state["closing"] = True
        plugins = state.get("plugins")
        if plugins is not None:
            plugins.close()
        controller = state.get("task_controller")
        if controller is not None:
            controller.close()
        for registry in list(getattr(state.get("task_runner"), "_jobs", {}).values()):
            registry.close_all()
        with self.resources:
            feed = state.get("runtime_updates")
            if feed is not None:
                feed.close()
            session_jobs = list(state.pop("jobs", {}).values())
        inbox = state.get("ling")
        if inbox is not None:
            inbox.close()
        for jobs in session_jobs:
            jobs.close_all()
        live = state.get("mcp")
        if live is not None:
            live.close()

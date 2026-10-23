"""Autonomy business logic and model-facing tools.

The host owns model execution, task facts, and the generic activity queue. This
module decides when the plugin wants an activity, prepares its context, and
records responsibility links without entering the host's database.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from nexus.plugins.api import BaseModel, EmptyArgs, Field, ToolSpec

from .store import PAUSABLE_TASK_STATES, PluginStore, now_iso


class ListWorkArgs(EmptyArgs):
    pass


class ReadWorkArgs(BaseModel):
    task_id: str = Field(min_length=1)
    section: str = "summary"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)
    text_offset: int = Field(default=0, ge=0)


class StartWorkArgs(BaseModel):
    goal: str = Field(min_length=1, max_length=20000)
    title: str = Field(default="", max_length=160)
    constraints: str = Field(default="", max_length=20000)
    attachments: list[str] | None = Field(default=None, max_length=8)
    responsibility_id: str | None = None
    report_requested: bool = False


class ControlWorkArgs(BaseModel):
    task_id: str = Field(min_length=1)
    operation: str


class ResponsibilityListArgs(EmptyArgs):
    pass


class ResponsibilitySaveArgs(BaseModel):
    id: str | None = None
    title: str | None = None
    instruction: str | None = None
    summary: str | None = None


class ResponsibilityControlArgs(BaseModel):
    id: str = Field(min_length=1)
    operation: str


class AutomationListArgs(EmptyArgs):
    pass


class AutomationFinishArgs(BaseModel):
    reply: str = ""
    summary: str = ""


def _json_result(value: dict[str, Any]) -> dict[str, Any]:
    return {"content": json.dumps(value, ensure_ascii=False, indent=1), **value}


def _work_service(context: Any) -> Any:
    service = getattr(context, "work_service", None)
    if service is None:
        raise PermissionError("工作工具只在灵的交互或自动活动中可用。")
    return service


class AutonomyRuntime:
    def __init__(self, host: Any, store: PluginStore | None = None) -> None:
        self.host = host
        legacy_home = Path(getattr(host, "legacy_home", host.data_dir))
        legacy_plugin_data = legacy_home / "plugins" / "autonomy"
        source = legacy_plugin_data / "autonomy.sqlite3"
        target = Path(host.data_dir) / "autonomy.sqlite3"
        if source.is_file() and not target.exists() and target.parent != source.parent:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        self.store = store or PluginStore(target)
        self._migrated = False
        self._service_configured = False
        self._last_error = ""
        self._legacy_rules: dict[str, Any] | None = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self.ensure_migrated()
        self.restore_legacy_holds()
        self.host.subscribe(["task.notification", "task.owner_control", "activity.finished"])
        self.configure_service()
        if "enabled" not in self.host.settings:
            settings = self.scheduler_snapshot().get("settings") or {}
            self.host.save_settings({**self.host.settings, "enabled": bool(settings.get("enabled", False))})
        self.catch_up_feedback()

    def close(self) -> None:
        return None


    def configure_service(self) -> None:
        if self._service_configured:
            return
        try:
            self.host.connect_service("scheduler", {
                "command": "{python}",
                "args": ["-m", "nexus_scheduler", "--data", "{data}/scheduler"],
                "env": {"PYTHONPATH": "{plugin}/services/scheduler/src"},
                "model_tools": [
                    "scheduler_state",
                    "scheduler_configure",
                    "schedule_save",
                    "schedule_wakeup",
                    "schedule_delete",
                    "schedule_run",
                    "watch_save",
                    "watch_delete",
                    "watch_check",
                ],
            })
            if self._legacy_rules:
                self.call_scheduler("scheduler_import", values=self._legacy_rules)
                self._legacy_rules = None
            self.call_scheduler("scheduler_connect", callback_url=self.host.event_url)
            self._service_configured = True
            self._last_error = ""
        except Exception as error:
            # Keep the plugin loaded so its management page can expose the
            # actual service error. A failed scheduler simply cannot wake Ling.
            self._last_error = f"{type(error).__name__}: {error}"

    def ensure_migrated(self) -> None:
        if self._migrated:
            return
        legacy = self.store.migrate_legacy(
            self.host.legacy_home,
            scheduler_path=self.host.data_dir / "scheduler" / "scheduler.sqlite3",
            notification_head=self._notification_head,
        )
        self._migrated = True
        legacy_rules = legacy.get("legacy_rules")
        if legacy_rules:
            self._legacy_rules = legacy_rules

    def restore_legacy_holds(self) -> None:
        """Restore eligibility guards for responsibilities paused before migration.

        The work is already paused in task storage, so restoring the hold avoids
        a second pause command while keeping the scheduler from dispatching it.
        """
        if self.store.meta("legacy_holds_restored") == "1":
            return
        for record in self.store.responsibilities():
            if record["state"] != "paused":
                continue
            for task_id in self.store.association_for(record["id"]):
                self.host.command("task.hold", id=task_id, held=True)
        self.store.meta("legacy_holds_restored", "1")

    def configure_scheduler(self, **values: Any) -> None:
        if values:
            self.host.call_service("scheduler", "scheduler_configure", values=values)

    def call_scheduler(self, tool: str, **args: Any) -> dict[str, Any]:
        value = self.host.call_service("scheduler", tool, **args)
        if not isinstance(value, dict):
            raise ValueError("调度服务返回了非对象结果")
        return value

    def scheduler_snapshot(self) -> dict[str, Any]:
        try:
            value = self.call_scheduler("scheduler_state")
            return {
                "settings": value.get("settings") or {},
                "schedules": value.get("schedules") or [],
                "watches": value.get("watches") or [],
                "error": value.get("error") or self._last_error,
            }
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            self._last_error = message
            return {"settings": {}, "schedules": [], "watches": [], "error": message}

    # --- task facts --------------------------------------------------------

    def tasks(self) -> list[dict[str, Any]]:
        return [dict(value) for value in (self.host.query("tasks.list") or [])]

    def task(self, task_id: str) -> dict[str, Any]:
        return dict(self.host.query("tasks.get", id=task_id))

    def _notification_head(self) -> int:
        """Adopt the current feed head once, without replaying old outcomes."""
        cursor = 0
        try:
            while True:
                page = self.host.query("tasks.notifications", after=cursor, limit=200) or []
                if not page:
                    break
                cursor = max(
                    cursor,
                    max((int(value.get("id") or 0) for value in page), default=cursor),
                )
                if len(page) < 200:
                    break
        except Exception:
            return cursor
        return cursor

    def responsibility_records(self) -> list[dict[str, Any]]:
        tasks = {task["id"]: task for task in self.tasks()}
        records = self.store.responsibilities()
        for record in records:
            record["tasks"] = [
                tasks[task_id] for task_id in record.pop("task_ids", []) if task_id in tasks
            ]
        return records

    # --- responsibilities -------------------------------------------------

    def list_responsibilities(self) -> dict[str, Any]:
        return {"responsibilities": self.responsibility_records()}

    def save_responsibility(
        self,
        *,
        responsibility_id: str | None = None,
        title: str | None = None,
        instruction: str | None = None,
        summary: str | None = None,
        origin: str = "user",
    ) -> dict[str, Any]:
        return self.store.save_responsibility(
            responsibility_id=responsibility_id,
            title=title,
            instruction=instruction,
            summary=summary,
            origin=origin,
        )

    def control_responsibility(self, responsibility_id: str, operation: str) -> dict[str, Any]:
        if operation not in ("pause", "resume", "end"):
            raise ValueError("长期交代操作必须是 pause、resume 或 end")
        current = self.store.get_responsibility(responsibility_id)
        if operation == "end":
            if current["state"] != "ended":
                self.store.set_responsibility_state(responsibility_id, "ended")
                for task_id in self.store.association_for(responsibility_id):
                    self.host.command("task.hold", id=task_id, held=False)
            return self.store.get_responsibility(responsibility_id)
        if operation == "resume":
            if current["state"] == "ended":
                raise ValueError("已结束的长期交代不能恢复")
            if current["state"] != "active":
                self.store.set_responsibility_state(responsibility_id, "active")
            for task_id in self.store.association_for(responsibility_id):
                self.host.command("task.hold", id=task_id, held=False)
            return self.store.get_responsibility(responsibility_id)
        if current["state"] == "ended":
            raise ValueError("已结束的长期交代不能暂停")
        if current["state"] != "paused":
            self.store.set_responsibility_state(responsibility_id, "paused")
        paused: list[str] = []
        associated = self.store.association_for(responsibility_id)
        for task_id in associated:
            self.host.command("task.hold", id=task_id, held=True)
        for task_id in associated:
            try:
                task = self.task(task_id)
            except Exception:
                continue
            if task.get("state") in ("completed", "cancelled"):
                continue
            if task.get("state") not in PAUSABLE_TASK_STATES or task.get("archived"):
                continue
            self.host.command("task.pause", id=task_id)
            paused.append(task_id)
        self.store.remember_paused_tasks(responsibility_id, paused)
        return self.store.get_responsibility(responsibility_id)

    def work(self) -> dict[str, Any]:
        try:
            return self._enrich_work(self.host.command("work.list"))
        except Exception:
            pass
        return self._enrich_work({"tasks": self.tasks()})

    def _enrich_work(self, value: dict[str, Any]) -> dict[str, Any]:
        result = dict(value)
        result["tasks"] = [
            {**task, **self.store.work_metadata(str(task.get("id") or ""))}
            for task in result.get("tasks") or []
        ]
        return result

    def read_task(
        self,
        task_id: str,
        section: str = "summary",
        offset: int = 0,
        limit: int = 20,
        text_offset: int = 0,
    ) -> dict[str, Any]:
        try:
            value = self.host.command(
                "work.read",
                task_id=task_id,
                section=section,
                offset=offset,
                limit=limit,
                text_offset=text_offset,
            )
            if section == "summary":
                value = {**value, **self.store.work_metadata(task_id)}
            return value
        except Exception:
            pass
        if section == "summary":
            task = self.task(task_id)
            return {
                **task,
                **self.store.work_metadata(task_id),
                "steps": task.get("steps") or [],
                "evidence_count": len(task.get("evidence") or []),
                "decision_count": len(task.get("decisions") or []),
            }
        if section == "messages":
            items = list(self.host.query("tasks.messages", id=task_id) or [])
        elif section == "evidence":
            task = self.task(task_id)
            items = [
                {"id": str(index + 1), **item}
                for index, item in enumerate(task.get("evidence") or [])
            ]
        elif section == "decisions":
            items = list(self.task(task_id).get("decisions") or [])
        elif section == "operations":
            items = list(self.host.query("tasks.operations", id=task_id) or [])
        else:
            raise ValueError("未知工作记录类型")
        page = [self._bounded(item, text_offset) for item in items[offset:offset + limit]]
        end = offset + len(page)
        return {
            "task_id": task_id,
            "section": section,
            "total": len(items),
            "items": page,
            "next_offset": end if end < len(items) else None,
        }

    @staticmethod
    def _bounded(value: Any, text_offset: int = 0) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"value": value}
        value = dict(value)
        offsets: dict[str, Any] = {}
        for key in ("text", "result_text", "summary"):
            text = value.get(key)
            if isinstance(text, str):
                end = min(len(text), text_offset + 6000)
                value[key] = text[text_offset:end]
                offsets[key] = {
                    "total_chars": len(text),
                    "next_offset": end if end < len(text) else None,
                }
        if offsets:
            value["text_offsets"] = offsets
        return value

    def start_work(self, context: Any, args: dict[str, Any]) -> dict[str, Any]:
        responsibility_id = args.get("responsibility_id")
        report_requested = bool(args.get("report_requested", False))
        if responsibility_id:
            record = self.store.get_responsibility(responsibility_id)
            if record["state"] != "active":
                raise ValueError("请先恢复这条长期交代，再启动新工作")
        values = {
            key: args[key]
            for key in ("goal", "title", "constraints", "attachments")
            if key in args
        }
        result = self.host.command("work.start", **values)
        task_id = str(result.get("id") or "")
        if not task_id:
            raise ValueError("宿主未返回新建工作标识")
        self.store.link_work(
            task_id,
            responsibility_id=responsibility_id,
            report_requested=report_requested,
        )
        return {**result, **self.store.work_metadata(task_id)}

    def control_work(self, context: Any, args: dict[str, Any]) -> dict[str, Any]:
        task_id = args["task_id"]
        operation = args["operation"]
        if operation not in ("pause", "resume", "cancel"):
            raise ValueError("工作操作必须是 pause、resume 或 cancel")
        result = self.host.command("work.control", task_id=task_id, operation=operation)
        if operation in ("resume", "cancel"):
            self.store.forget_paused_task(task_id)
        return result

    # --- activity and context ---------------------------------------------

    def context(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        kind = str(scope.get("kind") or "")
        if kind not in ("ling", "wake", "task", "subagent", "chat"):
            return []
        activity = scope.get("activity")
        run = self._activity_run(activity) if activity else None
        owns_activity = bool(run) and activity.get("plugin_id") in (None, self.host.id)
        materials: list[dict[str, Any]] = []
        ordinary = kind in ("ling", "wake") and not activity
        if ordinary or owns_activity:
            materials.append({
                "content": self._rules(run),
                "purpose": "instructions",
                "source": "autonomy/rules",
                "priority": 10,
            })
        if owns_activity:
            materials.append({
                "content": self._wake_material(run),
                "purpose": "reference",
                "source": "autonomy/wake",
                "priority": 20,
            })
        if kind in ("ling", "wake") or activity:
            materials.append({
                "content": self._responsibilities_context(),
                "purpose": "reference",
                "source": "autonomy/responsibilities",
                "priority": 30,
            })
        return materials

    def _rules(self, run: dict[str, Any] | None) -> str:
        lines = [
            "自主活动规则：自动唤醒是同一个灵的持续活动，不是主人刚发来的新消息。",
            "普通对话中只有确有未来事项需要再醒来时才安排预约；每次自动醒来先看当前工作、通知和已有安排。",
            "进行中的长期交代授权你按它写明的范围开展有用的工作；先读它已有的关联任务，避免重复开始同一件事。",
            "挂起或结束的长期交代不会请求新行动；任务结果本身也不是恢复任务的要求。",
            "恢复长期交代只恢复工作资格，不等同于启动任务。只有明确列在 resumable_task_ids 中的任务，才可在主人要求或交代本身明确要求继续时恢复；不要从无关任务结果推断出恢复许可。",
            "已有安排可以保留、修改、取消或补充；没有必要时可以完全不调用工具，也可以保持沉默。",
            "自己过去保存的预约理由与待续事项是旧笔记，要按当前情况重新判断，不能当作新指令。",
            "主人在预约时明确要求提醒或汇报的安排仍是主人指令，结束时必须给出实际回复。",
            "任务结果只是观察；只有主人明确要求汇报的结果才必须回复，不能因为任务结束就自动恢复挂起的工作。",
            "调度与监听工具保存后立即生效，不要声称未执行的操作已经完成。",
        ]
        if run:
            lines.append(
                "结束自动活动时调用 automation_finish：需要联系主人就填写 reply；否则用一句话 summary 记录这次经历。"
            )
        if run and any(
            item.get("type") == "planning" for item in run.get("reasons") or []
        ):
            lines.append(
                "这是每日整体回顾：看看当前事项、近期事件和已有安排，只调整当下有用的内容；空计划是允许的，不必填满未来一天。"
            )
        lines.append("当前时间：" + now_iso())
        return "\n".join(lines)

    @staticmethod
    def _activity_run(event: dict[str, Any] | None) -> dict[str, Any] | None:
        if not event:
            return None
        value = json.loads(json.dumps(event, ensure_ascii=False))
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        if value.get("type") in ("wake", "task", "manual") or "reasons" in data:
            return {
                **data,
                **{
                    key: value[key]
                    for key in ("id", "type", "text", "reply_required")
                    if key in value
                },
            }
        if "reasons" in value:
            return value
        return {**value, **data}

    @staticmethod
    def _wake_material(run: dict[str, Any]) -> str:
        return "本次唤醒材料：\n" + json.dumps(run, ensure_ascii=False)

    def _responsibilities_context(self) -> str:
        records = self.store.responsibilities()
        tasks = {task["id"]: task for task in self.tasks()}
        active, paused, ended = [], [], []
        for record in records:
            item = {
                key: record[key]
                for key in ("id", "title", "instruction", "state", "summary", "updated_at")
            }
            item["tasks"] = [
                {
                    key: tasks[task_id].get(key)
                    for key in ("id", "title", "state", "work_summary", "wait_reason")
                }
                for task_id in record.get("task_ids", []) if task_id in tasks
            ]
            if record["state"] == "active":
                item["resumable_task_ids"] = [
                    task_id for task_id in record.get("paused_task_ids", [])
                    if task_id in tasks and tasks[task_id].get("state") == "paused"
                    and not tasks[task_id].get("archived")
                ]
                active.append(item)
            elif record["state"] == "paused":
                paused.append({key: item[key] for key in ("id", "title", "state", "summary", "updated_at")})
            else:
                ended.append({key: item[key] for key in ("id", "title", "state", "summary", "updated_at")})
        value = {
            "active": active,
            "paused": paused,
            "ended": ended,
            "note": "挂起和结束的长期交代不会自动恢复工作；任务结果本身也不是恢复任务的要求。",
        }
        return "长期交代：\n" + json.dumps(value, ensure_ascii=False)

    # --- notification feedback --------------------------------------------

    def catch_up_feedback(self) -> None:
        cursor = self.store.feedback_cursor()
        while True:
            page = self.host.query("tasks.notifications", after=cursor, limit=200) or []
            if not page:
                break
            for notice in page:
                notice = self._notification(notice)
                notice_id = int(notice.get("id") or notice.get("notification_id") or 0)
                if notice_id <= 0:
                    continue
                reason = self._work_reason(notice)
                cursor = max(cursor, notice_id)
                if reason is None:
                    continue
                metadata = self.store.work_metadata(str(notice.get("task_id") or ""))
                reply_required = bool(metadata.get("report_requested")) and notice.get("kind") == "completed"
                if reply_required:
                    reason["report_requested"] = True
                self.host.emit({
                    "id": f"work-feedback:{notice_id}",
                    "type": "wake",
                    "text": str(notice.get("text") or ""),
                    "data": {
                        "source": "task-feedback",
                        "kind": "task",
                        "reasons": [reason],
                        "prompt": "",
                        "notify": reply_required,
                    },
                    "reply_required": reply_required,
                })
            self.store.set_feedback_cursor(cursor)
            if len(page) < 200:
                break

    @staticmethod
    def _notification(value: dict[str, Any]) -> dict[str, Any]:
        if isinstance(value.get("data"), dict) and (
            value.get("type") == "task.notification" or "task_id" not in value
        ):
            data = value["data"]
            if "task_id" in data:
                return {
                    **data,
                    "id": data.get("id") or value.get("external_id") or value.get("id"),
                }
        return value

    @staticmethod
    def _work_reason(notice: dict[str, Any]) -> dict[str, Any] | None:
        event = str(notice.get("event_type") or "")
        kind = str(notice.get("kind") or "")
        source = str(notice.get("source") or "")
        if kind == "completed":
            pass
        elif kind == "paused":
            if source not in ("agent", "error"):
                return None
        elif kind == "interrupted":
            if source not in ("", "error"):
                return None
        elif event == "interaction.pending":
            pass
        else:
            return None
        if kind == "completed":
            summary = (
                notice.get("work_summary") or notice.get("blocked")
                or notice.get("wait_reason") or notice.get("text") or ""
            )
        else:
            summary = (
                notice.get("wait_reason") or notice.get("blocked")
                or notice.get("work_summary") or notice.get("text") or ""
            )
        reason = {
            "type": "work_result",
            "notification_id": int(notice.get("id") or notice.get("notification_id") or 0),
            "task_id": str(notice.get("task_id") or ""),
            "state": str(notice.get("current_state") or notice.get("state") or ""),
            "summary": str(summary)[:4000],
            "source": source or event or kind,
            "kind": kind,
            "event": event,
        }
        if notice.get("interaction_id"):
            reason["interaction_id"] = str(notice["interaction_id"])
        if notice.get("wait_reason"):
            reason["wait_reason"] = str(notice["wait_reason"])[:2000]
        return reason

    def on_event(self, event: dict[str, Any]) -> None:
        name = str(event.get("type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if name in ("task.notification", "activity.finished"):
            self.catch_up_feedback()
        elif name == "task.owner_control":
            task_id = str(data.get("task_id") or "")
            if task_id:
                self.store.forget_paused_task(task_id)

    # --- tools -------------------------------------------------------------

    def tools(self, scope: dict[str, Any]) -> list[ToolSpec]:
        if str(scope.get("kind") or "") not in ("ling", "wake"):
            return []
        activity = scope.get("activity") or {}
        owns_activity = activity.get("plugin_id") in (None, self.host.id)

        def list_work(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            _work_service(context)
            return _json_result(self._enrich_work(self.host.command("work.list")))

        def read_work(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            _work_service(context)
            value = self.host.command("work.read", **args)
            if args.get("section", "summary") == "summary":
                value = {**value, **self.store.work_metadata(args["task_id"])}
            return _json_result(value)

        def start_work(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            _work_service(context)
            return _json_result(self.start_work(context, args))

        def control_work(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            _work_service(context)
            return _json_result(self.control_work(context, args))

        def list_responsibilities(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            return _json_result(self.list_responsibilities())

        def save_responsibility(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            return _json_result(self.save_responsibility(
                responsibility_id=args.get("id"),
                title=args.get("title"),
                instruction=args.get("instruction"),
                summary=args.get("summary"),
                origin="ling",
            ))

        def control_responsibility(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            return _json_result(self.control_responsibility(args["id"], args["operation"]))

        def automation_list(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            return _json_result(self.snapshot())

        def automation_finish(context: Any, args: dict[str, Any]) -> dict[str, Any]:
            result = self.host.complete_activity(
                reply=args.get("reply", ""), summary=args.get("summary", "")
            )
            return _json_result(result)

        specs = [
            ToolSpec(
                "work_list",
                "List actual independent work states; never starts or resumes work.",
                ListWorkArgs,
                list_work,
                read_only=True,
            ),
            ToolSpec(
                "work_read",
                "Read a work summary or page of messages, decisions or evidence. Use task ids from work_list.",
                ReadWorkArgs,
                read_work,
                read_only=True,
            ),
            ToolSpec(
                "work_start",
                "Start complex work independently. Set responsibility_id for an active responsibility and report_requested only when the owner explicitly wants a completion report. Returns immediately.",
                StartWorkArgs,
                start_work,
            ),
            ToolSpec(
                "work_control",
                "Request pause, resume or cancellation of one explicitly identified task.",
                ControlWorkArgs,
                control_work,
            ),
            ToolSpec(
                "responsibility_list",
                "List responsibilities and linked work. Paused and ended responsibilities are not running.",
                ResponsibilityListArgs,
                list_responsibilities,
                read_only=True,
            ),
            ToolSpec(
                "responsibility_save",
                "Create or update a responsibility Ling carries. New responsibilities are active and need title and instruction.",
                ResponsibilitySaveArgs,
                save_responsibility,
            ),
            ToolSpec(
                "responsibility_control",
                "Pause, resume or end a responsibility. Pause suspends its running or queued work; resume does not start work; end leaves existing work unchanged.",
                ResponsibilityControlArgs,
                control_responsibility,
            ),
            ToolSpec(
                "automation_list",
                "Read wake activity history, schedules, watches and responsibilities.",
                AutomationListArgs,
                automation_list,
                read_only=True,
            ),
        ]
        if owns_activity:
            specs.append(ToolSpec(
                "automation_finish",
                "End this automatic activity: reply for a useful owner message, otherwise summarize the experience in one sentence.",
                AutomationFinishArgs,
                automation_finish,
            ))
        return specs

    # --- page RPC ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        scheduler = self.scheduler_snapshot()
        return {
            **scheduler,
            "runs": self._runs(),
            "running": bool(self.host.query("activity.current")),
            "responsibilities": self.responsibility_records(),
            "error": scheduler.get("error") or self._last_error,
        }

    def _runs(self) -> list[dict[str, Any]]:
        events = self.host.query("activity.runs", limit=80) or []
        read = self.store.read_events()
        result = []
        for event in events:
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            legacy = data if "reasons" in data else {}
            result_value = event.get("result") if isinstance(event.get("result"), dict) else {}
            result.append({
                "id": event.get("external_id") or event.get("id"),
                "event_id": event.get("id"),
                "status": event.get("status"),
                "kind": legacy.get("kind") or data.get("kind") or event.get("type"),
                "prompt": legacy.get("prompt") or data.get("prompt") or event.get("text") or "",
                "notify": bool(legacy.get("notify") or event.get("reply_required")),
                "reasons": legacy.get("reasons") or data.get("reasons") or [],
                "reply": result_value.get("reply") or "",
                "summary": result_value.get("summary") or "",
                "error": event.get("error") or "",
                "created_at": event.get("created_at"),
                "finished_at": legacy.get("finished_at"),
                "read_at": (
                    read.get(str(event.get("id")))
                    or legacy.get("read_at")
                    or data.get("read_at")
                ),
            })
        return result

    def call(self, action: str, values: dict[str, Any]) -> dict[str, Any]:
        values = values or {}
        if action == "snapshot":
            return self.snapshot()
        if action == "settings.save":
            allowed = {
                "enabled", "interval_enabled", "interval_minutes",
                "planning_enabled", "planning_time", "watch_interval_minutes",
            }
            current = self.scheduler_snapshot()["settings"]
            merged = {**current, **{key: values[key] for key in values if key in allowed}}
            self.host.call_service("scheduler", "scheduler_configure", values=merged)
            self.host.save_settings({**self.host.settings, "enabled": bool(merged.get("enabled", False))})
            return self.snapshot()
        if action == "schedule.save":
            payload = {key: value for key, value in values.items() if value is not None}
            payload.setdefault("origin", "user")
            self.host.call_service("scheduler", "schedule_save", values=payload)
            return self.snapshot()
        if action == "schedule.delete":
            self.host.call_service("scheduler", "schedule_delete", id=values["id"])
            return self.snapshot()
        if action == "schedule.run":
            self.host.call_service("scheduler", "schedule_run", id=values["id"])
            return self.snapshot()
        if action == "watch.save":
            payload = {key: value for key, value in values.items() if value is not None}
            payload.setdefault("origin", "user")
            self.host.call_service("scheduler", "watch_save", values=payload)
            return self.snapshot()
        if action == "watch.delete":
            self.host.call_service("scheduler", "watch_delete", id=values["id"])
            return self.snapshot()
        if action == "watch.check":
            self.host.call_service("scheduler", "watch_check", id=values["id"])
            return self.snapshot()
        if action == "run.read":
            self.store.mark_run_read(str(values["id"]))
            return self.snapshot()
        if action == "responsibility.save":
            self.save_responsibility(
                responsibility_id=values.get("id"),
                title=values.get("title"),
                instruction=values.get("instruction"),
                summary=values.get("summary"),
                origin="user",
            )
            return self.snapshot()
        if action == "responsibility.control":
            self.control_responsibility(values["id"], values["operation"])
            return self.snapshot()
        raise ValueError("未知页面操作: " + action)


__all__ = ["AutonomyRuntime"]

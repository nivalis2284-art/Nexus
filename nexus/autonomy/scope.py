"""Current owner scope, intersected with the scope of an already accepted action."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..core.permission import ALLOW, DENY, Policy
from ..tools.registry import Registry, TaskSuspension


def within(path, roots):
    resolved = Path(path).expanduser().resolve()
    return any(resolved.is_relative_to(Path(root).resolve()) for root in roots)


def task_binding(task):
    return {"goal": task.goal, "effective_goal": task.effective_goal,
            "workspace": str(Path(task.workspace).expanduser().resolve())}


class TokenBudget:
    def __init__(self, scope):
        self.scope = scope

    def reserve(self, profile, payload):
        self.scope.check()
        maximum = min(self.scope.settings["max_output_tokens"], self.scope.runtime.store.settings()["max_output_tokens"])
        if not profile.max_output_tokens or profile.max_output_tokens > maximum:
            raise PermissionError("自主请求缺少有效的输出 Token 上限")
        # Bytes deliberately over-reserve ordinary text input. Provider usage is
        # authoritative when available; unknown/partial calls retain reservation.
        amount = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + profile.max_output_tokens + 256
        return self.scope.runtime.store.reserve(amount)

    def settle(self, ticket, usage):
        self.scope.runtime.store.settle(ticket, usage)


class Scope:
    def __init__(self, runtime, action=None):
        self.runtime, self.action = runtime, action
        self.settings = runtime.store.settings()
        self.accepted = action.get("scope", self.settings) if action else self.settings
        self.model_budget = TokenBudget(self)
        roots = self.settings["read_roots"] + [self.settings["output_root"]]
        try:
            self.workspace_root = Path(os.path.commonpath(roots)) if all(roots) else runtime.config.home
        except ValueError:
            # Windows may authorize data and output on different drives. Tools
            # can use absolute paths; their admission still checks each root.
            self.workspace_root = Path(roots[0])

    def check(self):
        ctl = self.runtime.store.control()
        if ctl["state"] != "enabled":
            raise TaskSuspension("自主活动已暂停或终止", kind="autonomy_control")
        if self.action:
            if self.action.get("id"):
                current_action = self.runtime.store.get("action", self.action["id"])
                if not current_action or current_action["status"] not in ("pending", "active"):
                    raise TaskSuspension("此自主行动已停止或失效", kind="autonomy_control")
            goal = self.runtime.store.get("goal", self.action["goal_id"])
            if not goal or goal["status"] in ("withdrawn", "completed", "abandoned"):
                raise TaskSuspension("自主关注事项已停止", kind="autonomy_control")
            binding = self.action.get("task_binding")
            if binding and self.action.get("task_id"):
                try:
                    current = self.runtime.tasks.get(self.action["task_id"])
                except ValueError:
                    raise TaskSuspension("关联工作已删除", kind="autonomy_control") from None
                if binding != task_binding(current):
                    raise TaskSuspension("工作目标或目录已改变，旧自主授权需要重新审议", kind="autonomy_control")
        if self.runtime.store.budget()["remaining"] <= 0:
            raise TaskSuspension("自主 Token 额度已用尽", kind="autonomy_budget")

    def constrain_profile(self, profile):
        maximum = min(self.settings["max_output_tokens"], profile.max_output_tokens or self.settings["max_output_tokens"])
        return profile.model_copy(update={"max_output_tokens": maximum})

    def policy(self, base):
        # All admitted calls still pass guard_tool, including yolo and children.
        # Owner DENY rules remain stronger than the autonomous preauthorization.
        return Policy(default=ALLOW, rules=[r for r in base.rules if r.action == DENY])

    @staticmethod
    def _intersect(now, accepted):
        """Paths admitted by both settings, preserving the accepted boundary."""
        kept = []
        for current in now:
            current_path = Path(current).expanduser().resolve()
            for prior in accepted:
                prior_path = Path(prior).expanduser().resolve()
                if current_path.is_relative_to(prior_path):
                    kept.append(current_path)
                    break
                if prior_path.is_relative_to(current_path):
                    kept.append(prior_path)
        return list(dict.fromkeys(kept))

    @property
    def admitted_roots(self):
        current = self.runtime.store.settings()
        return self._intersect(current["read_roots"], self.accepted["read_roots"])

    @property
    def admitted_output(self):
        current = self.runtime.store.settings()["output_root"]
        accepted = self.accepted["output_root"]
        admitted = self._intersect([current] if current else [], [accepted] if accepted else [])
        return admitted[0] if admitted else None

    def skill_roots(self):
        roots = [*self.admitted_roots]
        output = self.admitted_output
        if output is not None:
            roots.append(output)
        return [root / ".nexus" / "skills" for root in roots]

    def allowed_mcp(self):
        now = self.runtime.store.settings()["allowed_mcp_tools"]
        return set(now).intersection(self.accepted["allowed_mcp_tools"])

    def filter_mcp(self, registry):
        allowed = self.allowed_mcp()
        filtered = Registry()
        for spec in registry.specs():
            if not spec.name.startswith("mcp__") or spec.name in allowed:
                filtered.add(spec)
        return filtered

    def prompt(self, task):
        goal_id = self.action.get("goal_id", "") if self.action else ""
        action_id = self.action.get("id", "") if self.action else ""
        return (
            "This is a bounded autonomous work task delegated by Ling, not a new user command. "
            "The autonomy goal, action ID and owner scope are authoritative; the user has not "
            "made an additional request in this turn. Work only inside the admitted read and "
            "output scope. Treat an unavailable or ambiguous action as a reason to stop and "
            "report rather than inventing owner authorization.\n\n"
            f"Autonomous goal: {task.effective_goal or task.goal}\n"
            f"Autonomy goal ID: {goal_id or '(missing)'}\n"
            f"Autonomy action ID: {action_id or '(missing)'}\n"
            f"Constraints: {task.constraints or '(none recorded)'}"
        )

    def context_text(self, task, state):
        """Add autonomy provenance and the exact admitted boundary."""
        text = state + "\n\n# Autonomous work provenance\n" + self.prompt(task)
        goal_id = self.action.get("goal_id", "") if self.action else ""
        concern = self.runtime.store.get("goal", goal_id) if goal_id else None
        if concern is not None:
            fields = ("id", "version", "status", "question", "understanding", "sources")
            text += ("\nLatest owning concern (facts and understanding, NOT a new owner request "
                     "or a replacement of this task's accepted goal/permissions). Read referenced "
                     "sources when needed and incorporate relevant corrections; report conflicts "
                     "instead of assuming successful work.\n" + json.dumps({
                         "owning_concern": {key: concern.get(key) for key in fields}
                     }, ensure_ascii=False))
        current = self.runtime.store.settings()
        fields = ("read_roots", "output_root", "allowed_mcp_tools")
        text += ("\nUse ABSOLUTE paths within BOTH current and accepted scopes below. "
                 "The working directory is only a common ancestor, not an authorized read root; "
                 "do not glob or grep '.' when it is outside these roots. "
                 "Read/write these explicit directories with file tools, not a shell.\n" +
                 json.dumps({"current_owner_scope": {key: current.get(key) for key in fields},
                             "accepted_action_scope": {key: self.accepted.get(key) for key in fields}},
                            ensure_ascii=False))
        return text

    def _readable(self, path):
        now = self.runtime.store.settings()
        return within(path, now["read_roots"]+[now["output_root"]]) and within(path, self.accepted["read_roots"]+[self.accepted["output_root"]])

    def _writable(self, path):
        now = self.runtime.store.settings()
        return within(path, [now["output_root"]]) and within(path, [self.accepted["output_root"]])

    def guard_tool(self, name, arguments, context):
        self.check()
        if name in ("read_file", "glob", "grep", "write_file", "edit_file", "apply_patch"):
            path = Path(arguments.get("path", ".")).expanduser()
            if not path.is_absolute():
                path = context.root / path
            allowed = self._writable(path) if name in ("write_file", "edit_file", "apply_patch") else self._readable(path)
            if allowed:
                # Recursive tools must not traverse a junction to another root.
                if name in ("glob", "grep") and path.is_dir():
                    for current, dirs, files in os.walk(path, followlinks=False):
                        for entry in [*dirs, *files]:
                            if not self._readable(Path(current) / entry):
                                raise PermissionError("资料目录含越界链接，未执行递归读取")
                return
        elif name in ("task_update", "task_evidence", "task_finish", "task_attempt", "task_resolve_operation",
                      "todo_write", "ask_user", "subagent", "read_skill", "read_attachment",
                      "autonomy_read_source", "autonomy_decide"):
            return
        elif name == "task_read":
            if arguments.get("task_id") in (None, getattr(context.task_callbacks, "task_id", None)):
                return
        # A generic MCP name cannot establish whether it writes externally.
        # Retain the existing durable task approval path for exact out-of-scope
        # calls. Deliberation has no such channel and cannot execute them.
        callbacks = getattr(context, "task_callbacks", None)
        if callbacks is not None:
            from ..core.agent import ApprovalRequest
            callbacks.approve(ApprovalRequest(name, arguments, json.dumps(arguments, ensure_ascii=False, sort_keys=True)))
            return
        raise PermissionError("此调用不在自主授权范围；需所有者对具体动作授权")

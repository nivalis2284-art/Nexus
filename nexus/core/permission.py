"""Permission gate: one place that decides whether a call needs a human.

Rules are ordered, **last match wins**, and use simple glob matching on the value
being checked (a path for file tools, the command for bash). That is enough to
express "allow git status, ask for everything else" without a policy language.

Because the last match wins, the catch-all belongs **first** and the exceptions
after it::

    [{"tool": "bash", "pattern": "*",     "action": "ask"},
     {"tool": "bash", "pattern": "git *", "action": "allow"}]

Written the other way round, the ``*`` rule would override every rule above it.
This trips people up, so it is stated in the module docstring and in the tests.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


@dataclass
class Rule:
    tool: str
    pattern: str = "*"
    action: str = ASK


@dataclass
class Policy:
    default: str = ASK
    rules: list[Rule] = field(default_factory=list)
    # Answers the owner gave during this session, keyed by (tool, value).
    remembered: set[tuple[str, str]] = field(default_factory=set)

    def decide(self, tool: str, value: str) -> str:
        action = self.default
        for rule in self.rules:
            if rule.tool in (tool, "*") and fnmatch.fnmatch(value, rule.pattern):
                action = rule.action
        if action == ASK and (tool, value) in self.remembered:
            return ALLOW
        return action

    def remember(self, tool: str, value: str) -> None:
        self.remembered.add((tool, value))


def target(tool: str, arguments: dict) -> str:
    """The value a rule matches against."""
    if tool in ("read_file", "write_file", "edit_file", "apply_patch", "glob", "grep"):
        return str(arguments.get("path") or ".")
    if tool in ("bash", "bash_background"):
        return str(arguments.get("command") or "")
    if tool == "read_skill":
        return str(arguments.get("name") or "")
    return ""


def default_policy(mode: str = "ask", rules: list[dict] | None = None) -> Policy:
    """``ask`` gates writes and commands; ``yolo`` allows everything.

    ``rules`` (from configuration) replaces the built-in rule list when given.
    Without it the behaviour is exactly what it was before configuration
    existed, so an older config file keeps working unchanged.
    """
    if mode == "yolo":
        return Policy(default=ALLOW)
    parsed: list[Rule] = []
    for item in rules or []:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        tool = item.get("tool")
        if action not in (ALLOW, ASK, DENY) or not isinstance(tool, str) or not tool:
            continue
        pattern = item.get("pattern")
        parsed.append(
            Rule(tool=tool, pattern=str(pattern) if pattern else "*", action=action)
        )
    if parsed:
        return Policy(default=ALLOW, rules=parsed)
    return Policy(
        default=ALLOW,
        rules=[Rule(**item) for item in DEFAULT_RULES],
    )


DEFAULT_RULES = [
    {"tool": "write_file", "pattern": "*", "action": ASK},
    {"tool": "edit_file", "pattern": "*", "action": ASK},
    {"tool": "apply_patch", "pattern": "*", "action": ASK},
    {"tool": "bash", "pattern": "*", "action": ASK},
    {"tool": "bash_background", "pattern": "*", "action": ASK},
]

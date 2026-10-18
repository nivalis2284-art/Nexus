"""Permission gate: one place that decides whether a call needs a human.

Rules are ordered, last match wins, and use simple glob matching on the value
being checked (a path for file tools, the command for bash). That is enough to
express "allow git status, ask for everything else" without a policy language.
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
    if tool in ("read_file", "write_file", "edit_file", "glob", "grep"):
        return str(arguments.get("path") or ".")
    if tool == "bash":
        return str(arguments.get("command") or "")
    if tool == "read_skill":
        return str(arguments.get("name") or "")
    return ""


def default_policy(mode: str = "ask") -> Policy:
    """``ask`` gates writes and commands; ``yolo`` allows everything."""
    if mode == "yolo":
        return Policy(default=ALLOW)
    return Policy(
        default=ALLOW,
        rules=[
            Rule("write_file", "*", ASK),
            Rule("edit_file", "*", ASK),
            Rule("bash", "*", ASK),
        ],
    )

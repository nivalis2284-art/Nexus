"""Process-local entry point for the autonomy plugin."""

from __future__ import annotations

from .runtime import AutonomyRuntime


class AutonomyPlugin:
    def __init__(self, host) -> None:
        self.runtime = AutonomyRuntime(host)

    def start(self) -> None:
        self.runtime.start()

    def close(self) -> None:
        self.runtime.close()

    def tools(self, scope: dict):
        return self.runtime.tools(scope)

    def context(self, scope: dict):
        return self.runtime.context(scope)

    def on_event(self, event: dict) -> None:
        self.runtime.on_event(event)

    def call(self, action: str, values: dict) -> dict:
        return self.runtime.call(action, values)


def create_plugin(host) -> AutonomyPlugin:
    return AutonomyPlugin(host)


__all__ = ["AutonomyPlugin", "create_plugin"]

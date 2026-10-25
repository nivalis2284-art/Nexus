"""Public plugin authoring surface; business implementations live in plugins/."""
from __future__ import annotations

import threading
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from ..tools.registry import Registry, ToolResult, ToolSpec


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PluginHost:
    """A scoped facade. A plugin never needs the web server's mutable STATE."""

    def __init__(self, manager, plugin_id: str):
        self._manager, self.id = manager, plugin_id

    @property
    def data_dir(self):
        return self._manager.data_dir(self.id)

    @property
    def legacy_home(self):
        return self._manager.home

    @property
    def settings(self):
        settings = getattr(self._manager.local, 'settings', None)
        if settings is None:
            settings = self._manager.entries[self.id].settings
        return deepcopy(settings)

    @property
    def enabled(self):
        return self._manager.entries[self.id].enabled

    @property
    def base_url(self):
        return self._manager.base_url

    @property
    def event_url(self):
        return self.base_url + "/api/plugin-events/" + self.id

    def save_settings(self, values: dict):
        return self._manager.configure(self.id, values)

    def query(self, topic: str, **args):
        return self._manager.query(self.id, topic, **args)

    def command(self, action: str, **args):
        return self._manager.command(self.id, action, **args)

    def emit(self, payload: dict):
        return self._manager.receive(self.id, payload)

    def complete_activity(self, reply="", summary=""):
        return self.command("activity.finish", reply=reply, summary=summary)

    def connect_service(self, name: str, config: dict):
        return self._manager.connect_service(self.id, name, config)

    def call_service(self, name: str, tool: str, **args):
        return self._manager.call_service(self.id, name, tool, args)

    def subscribe(self, event_names: list[str]):
        self._manager.events.subscribe(self.id, event_names)

    def background(self, callback):
        """Keep an already-started plugin job leased until its callback finishes."""
        entered = threading.Event()
        failure = []

        def run():
            try:
                with self._manager.lease(self.id):
                    entered.set()
                    callback()
            except Exception as error:  # noqa: BLE001 -- expose job/lifecycle failure.
                failure.append(error)
                self._manager.entries[self.id].error = str(error)
            finally:
                entered.set()

        worker = threading.Thread(target=run, name='plugin-job-' + self.id, daemon=True)
        worker.start()
        entered.wait()
        if failure:
            raise failure[0]
        return worker


__all__ = ["BaseModel", "EmptyArgs", "Field", "PluginHost", "Registry", "ToolResult", "ToolSpec"]

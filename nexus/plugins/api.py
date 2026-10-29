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

    chat_api_version = 1
    hooks_api_version = 1

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

    def connect_service(self, name: str, config: dict):
        return self._manager.connect_service(self.id, name, config)

    def call_service(self, name: str, tool: str, **args):
        return self._manager.call_service(self.id, name, tool, args)

    def subscribe(self, event_names: list[str]):
        self._manager.events.subscribe(self.id, event_names)

    def define_hook(self, name: str, description: str = ''):
        """Declare plugin.<id>.<name>; returns the full hook name."""
        with self._manager.lock, self._manager.lease(self.id, allow_loading=True):
            return self._manager.hooks.define(self.id, name, description)

    def on_hook(self, name: str, callback, key: str = 'default'):
        """Register callback(payload, scope), identified by a stable local key."""
        with self._manager.lock, self._manager.lease(self.id, allow_loading=True):
            self._manager.hooks.subscribe(self.id, name, callback, key)

    def off_hook(self, name: str, key: str = 'default'):
        self._manager.hooks.unsubscribe(self.id, name, key)

    def emit_hook(self, name: str, payload: dict):
        """Notify subscribers of an owned hook; return delivery reports."""
        current = getattr(self._manager.local, 'call', None)
        context, scope = (current[1], current[2]) if current else (None, {})
        with self._manager.lease(self.id, context=context, scope=scope):
            with self._manager.lock:
                definition = self._manager.hooks.definitions.get(name)
                if not definition or definition['owner'] != self.id:
                    raise ValueError('插件只能触发自己声明的钩子')
            return self._manager.hooks.emit(name, payload, scope)

    def chat_snapshot(self):
        """Return the current application chat state."""
        with self._manager.lease(self.id, allow_loading=True):
            return self._chat_application().chat_snapshot()

    def chat_send(self, text, request_id):
        """Submit input through the same application path as the main chat."""
        with self._manager.lease(self.id, allow_loading=True):
            return self._chat_application().submit_input({'text': text, 'request_id': request_id})

    def chat_subscribe(self):
        """Subscribe to runtime updates until closed or the plugin is unloaded."""
        return self._manager.chat_subscribe(self.id)

    def _chat_application(self):
        application = self._manager.state.get('application')
        if application is None:
            raise RuntimeError('application is not available')
        return application

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

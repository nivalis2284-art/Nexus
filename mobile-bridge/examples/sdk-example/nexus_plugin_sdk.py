"""Minimal adapter for the public Nexus host chat API."""
from typing import Any, Protocol

SDK_VERSION = '0.2.0'


class ChatSubscription(Protocol):
    def get(self, timeout: float | None = None) -> dict | None: ...
    def close(self) -> None: ...
    def __enter__(self) -> 'ChatSubscription': ...
    def __exit__(self, *args: Any) -> None: ...


class PluginHost(Protocol):
    chat_api_version: int
    data_dir: Any
    settings: dict
    def save_settings(self, values: dict) -> Any: ...
    def chat_snapshot(self) -> dict: ...
    def chat_send(self, text: str, request_id: str) -> dict: ...
    def chat_subscribe(self) -> ChatSubscription: ...


class ChatClient:
    """Use the host's versioned chat interface directly."""
    def __init__(self, host: PluginHost):
        version = getattr(host, 'chat_api_version', None)
        if version != 1:
            raise RuntimeError(f'Nexus chat API version 1 is required (host provides {version!r})')
        self.host = host

    def snapshot(self):
        return self.host.chat_snapshot()

    def send(self, text: str, request_id: str):
        return self.host.chat_send(text, request_id)

    def open_events(self):
        """Subscribe before taking a snapshot; close the returned subscription.

        get(timeout) returns a raw event dict, raises queue.Empty on timeout, or
        returns None when closed. Reconnect with a fresh snapshot: no replay.
        """
        return self.host.chat_subscribe()

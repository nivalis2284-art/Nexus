"""Synchronous notification hooks, scoped to plugin lifetimes."""
from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass


@dataclass
class Subscription:
    plugin_id: str
    key: str
    callback: object
    required: bool = False
    error: str = ''


class HookRegistry:
    def __init__(self, manager):
        self.manager = manager
        self.path = manager.home / 'hooks.json'
        self.preferences = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.definitions = {
            'host.before_tool': {'owner': 'host', 'description': '工具执行前'},
            'host.after_tool': {'owner': 'host', 'description': '工具执行后'},
        }
        self.subscriptions = {}

    @staticmethod
    def validate_name(name):
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9_-]+(?:\.[a-z0-9_-]+)+', name):
            raise ValueError('钩子名称须由小写字母、数字、连字符或下划线组成，并以点分段')

    def define(self, plugin_id, name, description):
        full_name = f'plugin.{plugin_id}.{name}'
        self.validate_name(full_name)
        if not isinstance(description, str):
            raise ValueError('钩子说明须为文字')
        with self.manager.lock:
            self.definitions[full_name] = {'owner': plugin_id, 'description': description}
        return full_name

    def subscribe(self, plugin_id, name, callback, key, *, required=False):
        self.validate_name(name)
        if not callable(callback) or not isinstance(key, str) or not key:
            raise ValueError('订阅须提供回调及非空的稳定标识')
        with self.manager.lock:
            self.subscriptions.setdefault(name, {})[(plugin_id, key)] = Subscription(plugin_id, key, callback, required)

    def unsubscribe(self, plugin_id, name, key):
        with self.manager.lock:
            self.subscriptions.get(name, {}).pop((plugin_id, key), None)

    def remove_plugin(self, plugin_id):
        with self.manager.lock:
            self.definitions = {name: value for name, value in self.definitions.items()
                                if value['owner'] != plugin_id}
            for name in list(self.subscriptions):
                self.subscriptions[name] = {key: value for key, value in self.subscriptions[name].items()
                                            if value.plugin_id != plugin_id}
                if not self.subscriptions[name]:
                    del self.subscriptions[name]

    def _enabled(self, name, plugin_id=None, key=None):
        preference = self.preferences.get(name, {})
        if plugin_id is None:
            return preference.get('enabled', True)
        return preference.get('subscribers', {}).get(plugin_id, {}).get(key, True)

    def configure(self, name, enabled, plugin_id=None, key=None):
        if type(enabled) is not bool:
            raise ValueError('enabled 须为布尔值')
        with self.manager.lock:
            if name not in self.definitions and name not in self.subscriptions:
                raise ValueError('钩子不存在')
            if plugin_id is not None and (plugin_id, key) not in self.subscriptions.get(name, {}):
                raise ValueError('钩子订阅不存在')
            preferences = deepcopy(self.preferences)
            preference = preferences.setdefault(name, {})
            if plugin_id is None:
                preference['enabled'] = enabled
            else:
                preference.setdefault('subscribers', {}).setdefault(plugin_id, {})[key] = enabled
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            self.preferences = preferences
            return self.snapshot()

    def _ready(self, plugin_id):
        item = self.manager.entries.get(plugin_id)
        return bool(item and item.enabled and item.status == 'ready' and not self.manager.closing)

    def snapshot(self):
        with self.manager.lock:
            hooks = []
            for name in sorted(self.definitions.keys() | self.subscriptions.keys()):
                definition = self.definitions.get(name)
                owner = definition['owner'] if definition else None
                available = bool(definition and (owner == 'host' or self._ready(owner)))
                hooks.append({
                    'name': name, 'owner': owner, 'description': definition['description'] if definition else '',
                    'available': available, 'enabled': self._enabled(name),
                    'subscribers': [
                        {'plugin_id': sub.plugin_id, 'key': sub.key,
                         'enabled': self._enabled(name, sub.plugin_id, sub.key),
                         'available': self._ready(sub.plugin_id), 'error': sub.error, 'required': sub.required}
                        for _, sub in sorted(self.subscriptions.get(name, {}).items())],
                })
            return {'hooks': hooks}

    def emit(self, name, payload, scope=None):
        with self.manager.lock:
            definition = self.definitions.get(name)
            if definition is None:
                raise ValueError('钩子尚未声明: ' + name)
            if not self._enabled(name):
                return []
            owner = definition['owner']
            if owner != 'host' and not self._ready(owner):
                return []
            subscribers = sorted(self.subscriptions.get(name, {}).items())
        current = getattr(self.manager.local, 'call', None)
        scope = scope if scope is not None else (current[2] if current else {})
        context = current[1] if current else None
        reports = []
        for identity, sub in subscribers:
            # Grant a lifecycle lease atomically with checking the registration.
            with self.manager.lock:
                if (self.subscriptions.get(name, {}).get(identity) is not sub
                        or not self._enabled(name) or not self._enabled(name, *identity)
                        or not self._ready(sub.plugin_id)):
                    continue
                lease = self.manager.lease(sub.plugin_id, context=context, scope=scope)
                lease.__enter__()
            try:
                sub.callback(deepcopy(payload), deepcopy(scope))
                sub.error = ''
                reports.append({'plugin_id': sub.plugin_id, 'key': sub.key, 'ok': True})
            except Exception as error:  # noqa: BLE001 -- observers cannot interrupt the emitting operation.
                sub.error = f'{type(error).__name__}: {error}'
                self.manager.entries[sub.plugin_id].error = f'{name}: {sub.error}'
                reports.append({'plugin_id': sub.plugin_id, 'key': sub.key, 'ok': False, 'error': sub.error})
                if sub.required:
                    raise ValueError(f'必需的工具前置处理失败: {sub.plugin_id}: {error}') from error
            finally:
                lease.__exit__(None, None, None)
        return reports

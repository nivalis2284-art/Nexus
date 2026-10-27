from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
import threading
import uuid
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..mcp import ServerConfig
from .api import PluginHost
from .events import EventStore


@dataclass
class Entry:
    id: str
    path: Path
    manifest: dict
    settings: dict = field(default_factory=dict)
    enabled: bool = False
    status: str = 'unloaded'
    error: str = ''
    active_calls: int = 0
    instance: Any = None
    services: dict = field(default_factory=dict)
    python: Path | None = None
    pending_operation: str = ''
    progress: str = ''


class PluginManager:
    def __init__(self, state: dict, root: Path | None = None):
        self.state = state
        self.home = state['store'].home
        self.root = (root or Path(__file__).resolve().parents[2] / 'plugins').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries: dict[str, Entry] = {}
        self.lock = threading.RLock()
        self.local = threading.local()
        self.base_url = ''
        self.error = ''
        self.closing = False
        self._events_stop = threading.Event()
        self._events_thread = None
        self.events = EventStore(self.home / 'plugin-events.sqlite3')
        self.registry_file = self.home / 'plugins.json'
        self.saved = json.loads(self.registry_file.read_text(encoding='utf-8')) if self.registry_file.exists() else {}
        self.scan()

    def data_dir(self, plugin_id):
        path = self.home / 'plugins' / plugin_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save(self):
        if self.closing:
            return
        self.saved.update({key: {'enabled': item.enabled, 'settings': item.settings}
                           for key, item in self.entries.items()})
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.saved, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.registry_file)

    @staticmethod
    def read_manifest(path):
        value = json.loads((path / 'plugin.json').read_text(encoding='utf-8'))
        plugin_id = value.get('id', '')
        if not isinstance(plugin_id, str) or not plugin_id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in plugin_id):
            raise ValueError('插件标识仅支持小写字母、数字、连字符和下划线')
        if value.get('api_version', 1) != 1:
            raise ValueError('不支持此插件所需的宿主接口版本')
        return value

    def scan(self):
        with self.lock:
            for path in sorted(self.root.iterdir()):
                if path.name.startswith('.') or not path.is_dir() or not (path / 'plugin.json').is_file():
                    continue
                try:
                    manifest = self.read_manifest(path)
                    plugin_id = manifest['id']
                    if plugin_id in self.entries:
                        current = self.entries[plugin_id]
                        if current.path != path:
                            raise ValueError('插件标识重复')
                        if current.status == 'unloaded':
                            current.manifest = manifest
                        continue
                    saved = self.saved.get(plugin_id, {})
                    self.entries[plugin_id] = Entry(plugin_id, path, manifest,
                        settings=dict(saved.get('settings', {})), enabled=bool(saved.get('enabled', False)))
                except (OSError, ValueError) as error:
                    key = path.name
                    self.entries[key] = Entry(key, path, {'id': key, 'name': key}, status='error', error=str(error))
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            return {'root': str(self.root), 'error': self.error, 'plugins': [
                {**{key: item.manifest.get(key, default) for key, default in (
                    ('name', item.id), ('version', ''), ('description', ''), ('pages', []),
                    ('settings_schema', {}), ('capabilities', []))},
                 'id': item.id, 'enabled': item.enabled, 'status': item.status,
                 'error': item.error, 'active_calls': item.active_calls,
                 'settings': deepcopy(item.settings), 'progress': item.progress} for item in self.entries.values()]}

    def start(self, base_url):
        self.base_url = base_url
        tasks = self.state.get('tasks')
        if tasks:
            heads = tasks.change_heads()
            for key, column in (('task_cursor', 'notifications'), ('message_cursor', 'messages')):
                if self.events.metadata(key) is None:
                    self.events.metadata(key, heads[column])
        for item in list(self.entries.values()):
            if item.enabled:
                self.load(item.id)
        self._events_stop.clear()
        self._events_thread = threading.Thread(
            target=self._poll_events, name='plugin-events', daemon=True)
        self._events_thread.start()

    def _poll_events(self):
        while not self._events_stop.wait(.5):
            try:
                self.poll()
            except Exception as error:  # noqa: BLE001 -- keep the event worker alive.
                self.error = str(error)

    def load(self, plugin_id):
        with self.lock:
            item = self.entries[plugin_id]
            if item.status in ('loading', 'ready', 'draining'):
                return
            item.enabled, item.status, item.error = True, 'loading', ''
            self._save()
        threading.Thread(target=self._load, args=(item,), name='plugin-load-' + plugin_id, daemon=True).start()

    def _load(self, item):
        from .environment import prepare
        try:
            item.python = prepare(item.path, self.data_dir(item.id), item.manifest,
                                  on_progress=lambda message: setattr(item, 'progress', message))
            location = item.manifest.get('entry')
            if location:
                filename, _, factory = location.partition(':')
                target = self.asset(item.id, filename)
                name = 'nexus_extension_' + item.id.replace('-', '_')
                spec = importlib.util.spec_from_file_location(name, target,
                    submodule_search_locations=[str(target.parent)])
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                item.instance = getattr(module, factory or 'create_plugin')(PluginHost(self, item.id))
            for service in item.manifest.get('services', []):
                self.connect_service(item.id, service['name'], service)
            if item.instance and hasattr(item.instance, 'start'):
                item.instance.start()
            if item.manifest.get('subscriptions'):
                self.events.subscribe(item.id, item.manifest['subscriptions'])
            with self.lock:
                if item.enabled and not self.closing:
                    item.status = 'ready'
                    item.progress = ''
                    self.events.pause(item.id, False)
                else:
                    item.status = 'draining'
            if item.status == 'draining':
                self._cleanup(item)
        except Exception as error:  # noqa: BLE001 -- loading failures belong in plugin status.
            with self.lock:
                item.error = f'{type(error).__name__}: {error}'
                item.status = 'error'
            self._close_resources(item)

    def operate(self, plugin_id, operation):
        item = self.entries[plugin_id]
        if operation == 'load':
            self.load(plugin_id)
        elif operation in ('unload', 'restart', 'uninstall'):
            with self.lock:
                if item.status == 'draining' and item.pending_operation:
                    raise ValueError('插件正在收尾，请等待当前操作完成')
                item.enabled = False
                item.pending_operation = operation
                was_loading = item.status == 'loading'
                item.status = 'draining'
                self.events.pause(plugin_id, True)
                self._save()
            if not was_loading:
                self._cleanup(item)
        elif operation == 'stop':
            stopped = False
            callback = getattr(item.instance, 'stop', None)
            if callback:
                callback()
                stopped = True
            if not stopped:
                raise ValueError('当前没有可停止的插件在途工作')
        else:
            raise ValueError('未知插件操作')
        return self.snapshot()

    def _close_resources(self, item):
        try:
            if item.instance and hasattr(item.instance, 'close'):
                item.instance.close()
        finally:
            item.instance = None
            for name in list(item.services.values()):
                self.state['mcp'].remove(name)
            item.services.clear()

    def _cleanup(self, item):
        with self.lock:
            if item.active_calls or item.status != 'draining':
                return
            item.status = 'closing'
        try:
            self._close_resources(item)
            operation = item.pending_operation
            item.pending_operation = ''
            item.status = 'unloaded'
            if operation == 'uninstall':
                # Verify the actual target; imported directories always live under root.
                target = item.path.resolve()
                if target.parent != self.root:
                    raise ValueError('插件目录不在当前插件根目录中')
                shutil.rmtree(target)
                with self.lock:
                    self.entries.pop(item.id, None)
                    self.saved[item.id] = {'enabled': False, 'settings': item.settings}
            with self.lock:
                self._save()
            if operation == 'restart' and not self.closing:
                self.load(item.id)
        except Exception as error:  # noqa: BLE001 -- unloading must expose its actual failure.
            item.status, item.error = 'error', str(error)

    @contextmanager
    def lease(self, plugin_id, context=None, scope=None):
        item = self.entries[plugin_id]
        with self.lock:
            if not item.enabled or item.status != 'ready':
                raise ValueError('插件已停用或尚未就绪')
            item.active_calls += 1
            settings = deepcopy(item.settings)
        previous = getattr(self.local, 'call', None)
        previous_settings = getattr(self.local, 'settings', None)
        self.local.call = (plugin_id, context, scope or {})
        self.local.settings = settings
        try:
            yield item
        finally:
            self.local.call = previous
            self.local.settings = previous_settings
            with self.lock:
                item.active_calls -= 1
            if item.status == 'draining':
                self._cleanup(item)

    def enabled_ids(self):
        with self.lock:
            return [item.id for item in self.entries.values() if item.enabled and item.status == 'ready']

    def configure(self, plugin_id, values):
        with self.lock:
            item = self.entries[plugin_id]
            item.settings = deepcopy(values)
            self._save()
        return self.snapshot()

    def call(self, plugin_id, action, values=None, scope=None):
        with self.lease(plugin_id, scope=scope) as item:
            if not item.instance or not hasattr(item.instance, 'call'):
                raise ValueError('插件没有提供此操作接口')
            return item.instance.call(action, values or {})

    def expand(self, item, value):
        replacements = {'{plugin}': str(item.path), '{data}': str(self.data_dir(item.id)),
                        '{python}': str(item.python or sys.executable)}
        if isinstance(value, str):
            for key, replacement in replacements.items():
                value = value.replace(key, replacement)
        elif isinstance(value, list):
            value = [self.expand(item, v) for v in value]
        elif isinstance(value, dict):
            value = {k: self.expand(item, v) for k, v in value.items()}
        return value

    def connect_service(self, plugin_id, name, config):
        item = self.entries[plugin_id]
        full_name = 'plugin_' + plugin_id + '_' + name
        values = self.expand(item, {**config, 'name': full_name})
        cfg = ServerConfig.from_dict(values)
        if cfg is None:
            raise ValueError('插件服务声明缺少命令或地址')
        self.state['mcp'].add(cfg, cwd=item.path)
        item.services[name] = full_name
        server = self.state['mcp'].servers[full_name]
        server.plugin_id = plugin_id
        server.model_tools = values.get('model_tools', [])
        return {'name': full_name}

    def call_service(self, plugin_id, name, tool, args):
        item = self.entries[plugin_id]
        current = getattr(self.local, 'call', None)
        if not item.enabled and not (current and current[0] == plugin_id):
            raise ValueError('插件已停用')
        server = self.state['mcp'].servers.get(item.services.get(name))
        if server is None:
            raise ValueError('插件服务未连接')
        result = server.call_tool(tool, args)
        if not result.ok:
            raise ValueError(result.text)
        return result.structured_content if result.structured_content is not None else json.loads(result.text)

    def decorate(self, registry, context, scope):
        # Save original host operations before plugin-owned extensions replace a tool.
        context.plugin_original_tools = {spec.name: spec for spec in registry.specs()}
        for plugin_id in self.enabled_ids():
            item = self.entries[plugin_id]
            if not item.instance or not hasattr(item.instance, 'tools'):
                continue
            with self.lease(plugin_id, context, scope):
                additions = item.instance.tools(deepcopy(scope))
            for spec in additions:
                if registry.get(spec.name) and spec.name not in item.manifest.get('overrides', []):
                    raise ValueError(f'插件 {plugin_id} 工具重名: {spec.name}')
                def invoke(ctx, args, spec=spec, plugin_id=plugin_id):
                    with self.lease(plugin_id, ctx, scope):
                        return spec.handler(ctx, args)
                registry._tools[spec.name] = replace(spec, handler=invoke)
        context.plugin_manager = self
        context.plugin_scope = scope

    def prepare_agent(self, agent, messages):
        """Compose only a disposable view; never append recalls to saved history."""
        scope = {**getattr(agent, 'plugin_scope', {}), 'messages': deepcopy(messages)}
        from .. import skills
        if not hasattr(agent, '_plugin_base_skills'):
            agent._plugin_base_skills = getattr(agent, 'skills', '')
            agent._plugin_base_skill_bodies = dict(getattr(agent.context, 'skills', {}))
        agent.context.skills = dict(agent._plugin_base_skill_bodies)
        manifests = [agent._plugin_base_skills]
        for plugin_id in self.enabled_ids():
            found = skills.discover([self.entries[plugin_id].path / 'skills'])
            found.skills = {name: value for name, value in found.skills.items()
                            if name not in agent.context.skills}
            agent.context.skills.update(found.bodies())
            if found.skills:
                manifests.append(found.manifest())
        agent.skills = '\n'.join(value for value in manifests if value)
        if not hasattr(agent, '_plugin_base_tools'):
            mcp = getattr(agent.context, 'mcp', None)
            owned = {name for name, (server, _) in (mcp.tools.items() if mcp else [])
                     if getattr(server, 'plugin_id', None)}
            agent._plugin_base_tools = {key: spec for key, spec in agent.registry._tools.items() if key not in owned}
        agent.registry._tools = dict(agent._plugin_base_tools)
        from ..tools.mcp_tools import specs_for
        for spec in specs_for(agent.context):
            agent.registry._tools.setdefault(spec.name, spec)
        self.decorate(agent.registry, agent.context, {k: v for k, v in scope.items() if k != 'messages'})
        from ..core import compaction, prompt
        profile = getattr(agent, 'profile', None)
        if profile is not None:
            from ..core.effort import output_reserve
            budget = prompt.Budget(
                context_limit=agent.context_limit or profile.context_window_tokens or 64_000,
                output_reserve=output_reserve(profile, getattr(agent, 'effort', None)))
            base_system = prompt.build_system_prompt(
                agent.workspace, agent.registry.declarations(),
                platform=sys.platform, date='YYYY-MM-DD',
                role=agent.role, owner_instructions=agent.owner_context, skills=agent.skills,
                task_context=agent.task_context, interaction_context=agent.interaction_context)
            newest = scope['messages'][-1:]
            scope['material_budget_tokens'] = max(0, budget.input_limit
                - prompt.estimate(base_system) - prompt.estimate(json.dumps(agent.registry.schema()))
                - compaction.approx_tokens(newest) - 128)
        materials, reports = self.context(scope)
        # Dependency order is supplied by the manifest; presentation follows purpose.
        agent.request_materials = materials
        agent.request_plugins = reports
        agent.plugin_context = '\n\n'.join(
            f"[Plugin {value['plugin_id']}; source={value['source']}; purpose={value.get('purpose', 'reference')}]\n{value.get('content', '')}"
            for value in materials if value.get('content'))
        warnings = [f"插件 {report['id']} 的补充资料本次获取失败：{report['error']}。这不表示没有相关资料。"
                    for report in reports if report['status'] == 'failed']
        if warnings:
            agent.plugin_context += '\n\n[本次资料缺失说明]\n' + '\n'.join(warnings)
        return scope['messages']

    def context(self, scope):
        materials, reports = [], []
        ready = self.enabled_ids()
        required_ids = set(scope.get('required_plugins', []))
        missing = required_ids.difference(ready)
        if missing:
            raise ValueError('本次必需的插件已停用或未就绪: ' + ', '.join(sorted(missing)))
        reports.extend({'id': item.id, 'status': 'skipped', 'reason': '插件已停用或尚未就绪', 'required': False}
                       for item in self.entries.values() if item.id not in ready)
        for plugin_id in self.context_order():
            item = self.entries[plugin_id]
            required = plugin_id in required_ids
            callback = getattr(item.instance, 'context', None)
            remote = item.manifest.get('context')
            if not callback and not remote:
                continue
            try:
                with self.lease(plugin_id, scope=scope):
                    if callback:
                        result = callback(deepcopy(scope)) or []
                    else:
                        result = self.call_service(plugin_id, remote['service'], remote['tool'], {'scope': deepcopy(scope)})
                if isinstance(result, dict):
                    if 'messages' in result:
                        scope['messages'] = deepcopy(result['messages'])
                    result = result.get('materials', [])
                for material in result:
                    materials.append({**material, 'plugin_id': plugin_id, 'source': material.get('source') or plugin_id})
                reports.append({'id': plugin_id, 'status': 'used' if result else 'not_applicable', 'required': required})
            except Exception as error:
                item.error = str(error)
                reports.append({'id': plugin_id, 'status': 'failed', 'error': str(error), 'required': required})
                if required:
                    raise ValueError(f'必需的插件资料不可用: {plugin_id}: {error}') from error
        return materials, reports

    def context_order(self):
        ordered, visiting = [], set()
        available = self.enabled_ids()
        def visit(plugin_id):
            if plugin_id in ordered:
                return
            if plugin_id in visiting:
                raise ValueError('上下文参与依赖成环: ' + plugin_id)
            visiting.add(plugin_id)
            for predecessor in self.entries[plugin_id].manifest.get('context_after', []):
                if predecessor in available:
                    visit(predecessor)
            visiting.remove(plugin_id)
            ordered.append(plugin_id)
        for plugin_id in available:
            visit(plugin_id)
        return ordered

    def hook(self, hook_name, scope, **values):
        for plugin_id in self.enabled_ids():
            item = self.entries[plugin_id]
            callback = getattr(item.instance, hook_name, None)
            if callback:
                try:
                    with self.lease(plugin_id, scope=scope):
                        callback(scope=deepcopy(scope), **values)
                except Exception as error:
                    item.error = f'{hook_name}: {error}'
                    if hook_name == 'before_tool' and item.manifest.get('tool_hook_required', False):
                        raise ValueError(f'必需的工具前置处理失败: {plugin_id}: {error}') from error

    def publish(self, event_type, data, event_id=None):
        self.events.publish(event_type, data, event_id)

    def poll(self):
        tasks = self.state.get('tasks')
        if tasks:
            cursor = int(self.events.metadata('task_cursor') or 0)
            for notice in tasks.notifications(after=cursor, limit=100):
                self.publish('task.notification', notice, 'task-notice-' + str(notice['id']))
                cursor = notice['id']
                self.events.metadata('task_cursor', cursor)
            cursor = int(self.events.metadata('message_cursor') or 0)
            for message in tasks.message_changes(after=cursor, limit=100):
                self.publish('message.saved', message, 'task-message-' + str(message['seq']))
                cursor = message['seq']
                self.events.metadata('message_cursor', cursor)
        for plugin_id in self.enabled_ids():
            item = self.entries[plugin_id]
            for change in self.events.changes(plugin_id):
                if change['subscribed']:
                    try:
                        with self.lease(plugin_id):
                            value = {k: v for k, v in change.items() if k != 'subscribed'}
                            if hasattr(item.instance, 'on_event'):
                                item.instance.on_event(value)
                            elif item.manifest.get('event_handler'):
                                remote = item.manifest['event_handler']
                                self.call_service(plugin_id, remote['service'], remote['tool'], {'event': value})
                    except Exception as error:  # noqa: BLE001 -- keep this subscription cursor until callback succeeds.
                        item.error = str(error)
                        break
                self.events.ack(plugin_id, change['seq'])

    def receive(self, plugin_id, payload):
        if plugin_id not in self.entries:
            raise ValueError('插件尚未登记')
        item = self.entries[plugin_id]
        return self.events.add(plugin_id, payload, item.enabled and item.status == 'ready')

    def query(self, plugin_id, topic, **args):
        tasks = self.state.get('tasks')
        if topic == 'state':
            return {'plugins': self.snapshot()}
        if topic == 'tasks.list':
            return [task.to_dict() for task in tasks.list()]
        if topic == 'tasks.get':
            return tasks.get(args.get('id') or args.get('task_id')).to_dict()
        if topic == 'tasks.messages':
            return tasks.messages(args.get('id') or args.get('task_id'))
        if topic == 'tasks.operations':
            return tasks.unfinished_operations(args.get('id') or args.get('task_id'))
        if topic == 'tasks.notifications':
            return tasks.notifications(after=args.get('after', 0), limit=args.get('limit', 100))
        if topic == 'sessions.messages':
            session = self.state['sessions'].load(args['id'])
            return [{'kind': value.kind, 'text': value.text, 'data': value.data} for value in session.items] if session else []
        if topic == 'sessions.records':
            session = self.state['sessions'].load(args['id'])
            if session is None:
                raise ValueError('会话不存在')
            return [{key: value for key, value in item.to_record().items() if key != 'thinking'}
                    for item in session.items]
        if topic == 'ling.identity':
            runtime = self.state.get('ling')
            if runtime and getattr(runtime, 'session_id', None):
                return {'session_id': runtime.session_id}
            from ..ling import ling_session_id
            return {'session_id': ling_session_id(tasks)}
        raise ValueError('未知宿主查询: ' + topic)

    def command(self, plugin_id, action, **args):
        current = getattr(self.local, 'call', None)
        if action.startswith('work.'):
            context = current[1] if current else None
            service = getattr(context, 'work_service', None)
            if service is None:
                raise ValueError('当前调用没有工作监督上下文')
            return getattr(service, action.split('.')[1])(**args)
        if action in ('task.pause', 'task.resume', 'task.cancel'):
            task_id = args.get('id') or args.get('task_id')
            controller, store = self.state['task_controller'], self.state['tasks']
            task = store.get(task_id)
            getattr(controller, action.split('.')[1])(task_id,
                request_id=args.get('request_id') or f'plugin-{plugin_id}-{uuid.uuid4().hex}',
                expected_version=task.control_version)
            return store.get(task_id).to_dict()
        raise ValueError('未知宿主操作: ' + action)

    def asset(self, plugin_id, relative):
        item = self.entries[plugin_id]
        target = (item.path / relative).resolve()
        if not target.is_relative_to(item.path.resolve()) or not target.is_file():
            raise ValueError('插件文件不存在或不属于此插件')
        return target

    def _remove_directory(self, path):
        target = path.resolve()
        if target.parent != self.root:
            raise ValueError('待移除目录不在插件根目录中')
        shutil.rmtree(target)

    def import_zip(self, content: bytes, replace_existing=False):
        staging = self.root / ('.import-' + uuid.uuid4().hex)
        staging.mkdir()
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for info in archive.infolist():
                    target = (staging / info.filename).resolve()
                    if not target.is_relative_to(staging.resolve()):
                        raise ValueError('插件包包含目录外文件')
                archive.extractall(staging)
            candidates = list(staging.glob('plugin.json')) + list(staging.glob('*/plugin.json'))
            if len(candidates) != 1:
                raise ValueError('插件包应包含唯一的 plugin.json')
            source = candidates[0].parent
            manifest = self.read_manifest(source)
            plugin_id = manifest['id']
            previous = self.entries.get(plugin_id)
            if previous and not replace_existing:
                return {'conflict': True, 'error': '该插件已安装，请确认替换', 'plugin': manifest,
                        'installed_version': previous.manifest.get('version', '')}
            if previous and previous.status not in ('unloaded', 'error'):
                raise ValueError('请先停用插件并等待收尾，再更新安装包')
            target = previous.path if previous else self.root / plugin_id
            backup = self.root / ('.previous-' + uuid.uuid4().hex)
            if target.exists():
                target.rename(backup)
            try:
                source.rename(target)
            except Exception:
                if backup.exists():
                    backup.rename(target)
                raise
            if backup.exists():
                self._remove_directory(backup)
            if previous:
                previous.manifest = manifest
                previous.error, previous.status = '', 'unloaded'
            else:
                self.scan()
            return self.snapshot()
        finally:
            if staging.exists():
                self._remove_directory(staging)

    def close(self):
        self.closing = True
        self._events_stop.set()
        if self._events_thread and self._events_thread is not threading.current_thread():
            self._events_thread.join(timeout=5)
        for item in list(self.entries.values()):
            if item.instance or item.services:
                self.operate(item.id, 'unload')

"""Check the shared SDK is embedded while local development data stays out."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest


def test_copied_sdk_builds_examples_and_loads_them(tmp_path):
    from nexus.plugins.manager import PluginManager
    from nexus.tools import build

    sdk = tmp_path / 'standalone-sdk'
    shutil.copytree(Path(__file__).resolve().parents[1] / 'nexus-plugin-sdk', sdk,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.zip'))
    manager = PluginManager({'store': SimpleNamespace(home=tmp_path / 'home')}, root=tmp_path / 'installed')
    try:
        for folder, plugin_id in [('tool-skill', 'example-tool-skill'), ('host-page', 'example-host-page')]:
            example = sdk / 'examples' / folder
            subprocess.run([sys.executable, str(sdk / 'build.py'), str(example)],
                           cwd=tmp_path, check=True, capture_output=True)
            archive = next(example.glob('nexus-*.zip'))
            with zipfile.ZipFile(archive) as package:
                assert package.testzip() is None
                assert json.loads(package.read('plugin.json'))['id'] == plugin_id
            manager.import_zip(archive.read_bytes())
            manager.load(plugin_id)
            deadline = time.monotonic() + 3
            while manager.entries[plugin_id].status == 'loading' and time.monotonic() < deadline:
                time.sleep(.01)
            assert manager.entries[plugin_id].status == 'ready', manager.entries[plugin_id].error
        registry, context = build(root=tmp_path)
        agent = SimpleNamespace(registry=registry, context=context, profile=None, skills='', plugin_scope={})
        manager.prepare_agent(agent, [{'role': 'user', 'content': 'status'}])
        assert registry.execute('example_status', {}, context).content == '示例插件已启用。'
        assert 'example_status' in registry.execute('read_skill', {'name': 'example-workflow'}, context).content
        page = manager.page('example-host-page', 'main', {'event': 'action', 'action': 'show', 'values': {'text': 'hello'}})
        assert page['components'][-1]['text'] == 'hello'
    finally:
        manager.close()


@pytest.mark.parametrize('client,code', [
    ('nexus_host.py', 'from nexus_host import HostClient'),
    ('nexus-host.mjs', "import {HostClient} from './nexus-host.mjs';"),
])
def test_shared_sdk_packaging(tmp_path, monkeypatch, client, code):
    spec = importlib.util.spec_from_file_location('plugin_packaging', Path(__file__).resolve().parents[1] / 'plugin-dev/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module.sdk, 'SDK_ROOT', tmp_path)
    source = tmp_path / 'example'
    source.mkdir()
    (source / 'plugin.json').write_text(json.dumps({'id': 'example', 'version': '0.1.0'}))
    (source / 'README.md').write_text('# Example\nPlugin introduction', encoding='utf-8')
    (source / ('backend.py' if client.endswith('.py') else 'worker.mjs')).write_text(code)
    (source / 'credentials.json').write_text('{"secret":"local"}')
    (source / 'tests').mkdir()
    (source / 'tests/test_example.py').write_text('assert False')
    for name in (client, 'SDK.md', 'UI.md'):
        (tmp_path / name).write_text('canonical ' + name)
    with zipfile.ZipFile(module.build('example')) as archive:
        assert archive.read(client).decode() == 'canonical ' + client
        assert 'plugin.json' in archive.namelist()
        assert archive.read('README.md') == (source / 'README.md').read_bytes()
        assert 'credentials.json' not in archive.namelist()
        assert not any(name.startswith('tests/') for name in archive.namelist())


@pytest.mark.parametrize('content', [None, '', ' \n\t', '\ufeff'])
def test_packaging_requires_introduction(tmp_path, monkeypatch, content):
    spec = importlib.util.spec_from_file_location('plugin_packaging', Path(__file__).resolve().parents[1] / 'plugin-dev/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    source = tmp_path / 'example'
    source.mkdir()
    if content is not None:
        (source / 'README.md').write_text(content, encoding='utf-8')
    with pytest.raises(ValueError, match='README.md'):
        module.build('example')
    assert not list(source.glob('*.zip'))


@pytest.mark.parametrize('with_tool', [False, True], ids=['skills-only', 'tools-and-skills'])
def test_packaged_skills_follow_plugin_lifecycle(tmp_path, monkeypatch, with_tool):
    from nexus.plugins.manager import PluginManager
    from nexus.tools import build

    spec = importlib.util.spec_from_file_location('plugin_packaging', Path(__file__).resolve().parents[1] / 'plugin-dev/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dev = tmp_path / 'dev'
    source = dev / 'example'
    source.mkdir(parents=True)
    monkeypatch.setattr(module, 'ROOT', dev)
    monkeypatch.setattr(module.sdk, 'SDK_ROOT', dev)
    for name in ('SDK.md', 'UI.md'):
        (dev / name).write_text('test packaging documentation', encoding='utf-8')
    manifest = {'id': 'example', 'version': '1.0'}
    if with_tool:
        manifest['entry'] = 'backend.py:create_plugin'
        (source / 'backend.py').write_text('''
from nexus.plugins.api import EmptyArgs, ToolSpec
class Plugin:
    def tools(self, scope):
        return [ToolSpec('example_status', 'Read status', EmptyArgs,
                         lambda ctx, args: {'content': 'ready'}, read_only=True)]
def create_plugin(host): return Plugin()
''', encoding='utf-8')
    (source / 'plugin.json').write_text(json.dumps(manifest), encoding='utf-8')
    (source / 'README.md').write_text('# Example', encoding='utf-8')
    skill = source / 'skills' / 'example-workflow' / 'SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: example-workflow\ndescription: Example workflow\n---\nCall example_status.', encoding='utf-8')
    reference = skill.parent / 'references' / 'usage.md'
    reference.parent.mkdir()
    reference.write_text('Bundled reference', encoding='utf-8')
    (source / 'skills' / 'project-rule.md').write_text('---\nname: project-rule\ndescription: Plugin rule\n---\nPlugin rule.', encoding='utf-8')
    archive = module.build('example')
    with zipfile.ZipFile(archive) as package:
        assert package.read('skills/example-workflow/references/usage.md') == b'Bundled reference'

    home = tmp_path / 'home'
    manager = PluginManager({'store': SimpleNamespace(home=home)}, root=tmp_path / 'installed')
    registry, context = build({'project-rule': 'Workspace rule.'}, root=tmp_path)
    agent = SimpleNamespace(registry=registry, context=context, profile=None,
                            skills='- project-rule: Workspace rule', plugin_scope={})

    def ready():
        deadline = time.monotonic() + 3
        while manager.entries['example'].status == 'loading' and time.monotonic() < deadline:
            time.sleep(.01)
        assert manager.entries['example'].status == 'ready', manager.entries['example'].error

    def prepare():
        manager.prepare_agent(agent, [{'role': 'user', 'content': 'Check status'}])

    try:
        manager.import_zip(archive.read_bytes())
        prepare()
        assert 'example-workflow' not in context.skills
        manager.load('example')
        ready()
        prepare()
        assert 'example-workflow: Example workflow' in agent.skills
        assert 'Call example_status.' not in agent.skills
        assert registry.execute('read_skill', {'name': 'example-workflow'}, context).content == 'Call example_status.'
        assert context.skills['project-rule'] == 'Workspace rule.'
        if with_tool:
            assert registry.execute('example_status', {}, context).content == 'ready'
        manager.operate('example', 'unload')
        prepare()
        assert 'example-workflow' not in agent.skills and 'example-workflow' not in context.skills
        assert not registry.execute('read_skill', {'name': 'example-workflow'}, context).ok
        assert registry.get('example_status') is None
        manager.load('example')
        ready()
        manifest['version'] = '2.0'
        (source / 'plugin.json').write_text(json.dumps(manifest), encoding='utf-8')
        skill.write_text(skill.read_text(encoding='utf-8').replace('Call example_status.', 'Updated workflow.'), encoding='utf-8')
        manager.import_zip(module.build('example').read_bytes(), replace_existing=True)
        ready()
        prepare()
        assert registry.execute('read_skill', {'name': 'example-workflow'}, context).content == 'Updated workflow.'
        assert manager.entries['example'].manifest['version'] == '2.0'
    finally:
        manager.close()

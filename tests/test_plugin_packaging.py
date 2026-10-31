"""Check the shared SDK is embedded while local development data stays out."""
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


@pytest.mark.parametrize('client,code', [
    ('nexus_host.py', 'from nexus_host import HostClient'),
    ('nexus-host.mjs', "import {HostClient} from './nexus-host.mjs';"),
])
def test_shared_sdk_packaging(tmp_path, monkeypatch, client, code):
    spec = importlib.util.spec_from_file_location('plugin_packaging', Path(__file__).resolve().parents[1] / 'plugin-dev/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
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

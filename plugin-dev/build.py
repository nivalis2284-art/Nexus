"""Package one plugin, using the single shared SDK source. Never installs it."""
from pathlib import Path
import argparse
import json
import zipfile

ROOT = Path(__file__).resolve().parent


def build(plugin):
    source = (ROOT / plugin).resolve()
    if source.parent != ROOT:
        raise ValueError('请选择 plugin-dev 下的一个插件目录')
    readme = source / 'README.md'
    if not readme.is_file() or not readme.read_text(encoding='utf-8-sig').strip():
        raise ValueError('插件根目录须提供非空的 UTF-8 README.md 介绍文档')
    manifest = json.loads((source / 'plugin.json').read_text(encoding='utf-8'))
    files = {p.name: p for p in source.iterdir()
             if p.is_file() and (p.suffix in ('.py', '.mjs')
                                or p.name in ('plugin.json', 'view.json', 'README.md', 'LICENSE')) and p.name != 'build.py'}
    for directory in ('web', 'docs'):
        for path in (source / directory).rglob('*'):
            if path.is_file():
                files[path.relative_to(source).as_posix()] = path
    for name in ('nexus_host.py', 'nexus-host.mjs'):
        module = name.removesuffix('.py') if name.endswith('.py') else name
        if any(module in path.read_text(encoding='utf-8') for path in files.values()
               if path.suffix in ('.py', '.mjs')):
            files[name] = ROOT / name
    files['SDK.md'] = ROOT / 'SDK.md'
    files['UI.md'] = ROOT / 'UI.md'
    output = source / f'nexus-{manifest["id"]}-{manifest["version"]}.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read('plugin.json')) == manifest
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plugin', help='插件子目录名，例如 qq-bridge')
    print(build(parser.parse_args().plugin))

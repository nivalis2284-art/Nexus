"""Package a Nexus plugin from a directory; never install it."""
import argparse
import json
from pathlib import Path
import zipfile

SDK_ROOT = Path(__file__).resolve().parent


def build(plugin):
    source = Path(plugin).resolve()
    readme = source / 'README.md'
    if not readme.is_file() or not readme.read_text(encoding='utf-8-sig').strip():
        raise ValueError('插件根目录须提供非空的 UTF-8 README.md 介绍文档')
    manifest = json.loads((source / 'plugin.json').read_text(encoding='utf-8'))
    files = {p.name: p for p in source.iterdir()
             if p.is_file() and (p.suffix in ('.py', '.mjs')
                                or p.name in ('plugin.json', 'view.json', 'README.md', 'LICENSE')) and p.name != 'build.py'}
    for directory in ('web', 'docs', 'skills'):
        for path in (source / directory).rglob('*'):
            if path.is_file():
                files[path.relative_to(source).as_posix()] = path
    for name in ('nexus_host.py', 'nexus-host.mjs'):
        module = name.removesuffix('.py') if name.endswith('.py') else name
        if any(module in path.read_text(encoding='utf-8') for path in files.values()
               if path.suffix in ('.py', '.mjs')):
            files[name] = SDK_ROOT / name
    files['SDK.md'] = SDK_ROOT / 'SDK.md'
    files['UI.md'] = SDK_ROOT / 'UI.md'
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
    parser.add_argument('plugin', help='插件目录路径')
    print(build(parser.parse_args().plugin))

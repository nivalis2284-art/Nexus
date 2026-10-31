"""Build the independent plugin ZIP without installing or touching the host."""
from pathlib import Path
import json
import zipfile

ROOT = Path(__file__).resolve().parent
FILES = ['plugin.json', 'backend.py', 'messages.py', 'media.py', 'qq.py', 'review.py',
         'store.py', 'README.md', 'LICENSE', 'docs/VALIDATION.md',
         'web/index.html', 'web/app.js', 'web/style.css']


def build():
    manifest = json.loads((ROOT / 'plugin.json').read_text(encoding='utf-8'))
    destination = ROOT.parent / 'releases' / 'plugins' / f'nexus-qq-bridge-{manifest["version"]}.zip'
    assert destination.resolve().is_relative_to(ROOT.parent.resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, name)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(FILES)
        assert json.loads(archive.read('plugin.json'))['id'] == 'qq-bridge'
        assert not any('credentials' in name or 'sqlite' in name or name.startswith('.') for name in archive.namelist())
    return destination


if __name__ == '__main__':
    print(build())

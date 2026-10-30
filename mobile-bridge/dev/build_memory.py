"""Build the curated memory plugin archive; does not install or start Nexus."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parent
source = root / 'memory'
manifest = json.loads((source / 'plugin.json').read_text(encoding='utf-8'))
output = root.parent / 'releases' / 'plugins' / ('nexus-memory-' + manifest['version'] + '.zip')
files = [source / name for name in ('plugin.json', 'README.md', 'backend.py', 'service.py', 'models.py', 'store.py', 'provider.py', 'engine.py')]
files.extend(sorted((source / 'web').glob('*')))
output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        archive.write(path, path.relative_to(source).as_posix())
print(json.dumps({'path': str(output), 'files': len(files), 'bytes': output.stat().st_size, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))

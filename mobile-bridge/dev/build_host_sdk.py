"""Curated SDK/example packages. Does not install or enable plugins."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[1]
output = root / 'releases' / 'plugins'
output.mkdir(parents=True, exist_ok=True)
packages = {
    'nexus-host-sdk-python-1.1.0.zip': root/'examples'/'host-sdk-python',
    'nexus-host-sdk-node-1.1.0.zip': root/'examples'/'host-sdk-node',
}
for name, source in packages.items():
    with zipfile.ZipFile(output/name,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(source.iterdir()):
            if path.is_file() and path.suffix in ('.py','.mjs','.html','.json','.md'):
                z.write(path,path.name)
        z.write(root/'sdk/v1/README.md','SDK.md')
        z.write(root/'sdk/v1/UI.md','UI.md')
    print(json.dumps({'file':name,'sha256':hashlib.sha256((output/name).read_bytes()).hexdigest()}))
with zipfile.ZipFile(output/'nexus-plugin-sdk-1.1.0.zip','w',zipfile.ZIP_DEFLATED) as z:
    for path in sorted((root/'sdk/v1').iterdir()):
        if path.is_file() and path.suffix in ('.py','.mjs','.md'):z.write(path,path.name)
    for source in packages.values():
        for path in sorted(source.iterdir()):
            if path.is_file() and path.suffix in ('.py','.mjs','.html','.json','.md'):
                z.write(path,'examples/'+source.name+'/'+path.name)
print(json.dumps({'file':'nexus-plugin-sdk-1.1.0.zip','sha256':hashlib.sha256((output/'nexus-plugin-sdk-1.1.0.zip').read_bytes()).hexdigest()}))

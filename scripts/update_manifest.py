"""Regenerate the install manifest from an explicitly staged release file list."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
files = []
for name in sorted(name for name in names if name and name != 'manifest.json'):
    raw = (ROOT / name).read_bytes()
    files.append({'path': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
if not files:
    raise ValueError('Stage the intended release files before generating the manifest')
manifest = {'format_version': 1, 'package': 'ths-reader-v0.1.1', 'files': files,
            'excluded': ['Account data and logs', 'Personal finance files', 'Local configuration',
                         'Client binaries', 'Private evidence and Git metadata'],
            'dependency_policy': 'Original Frida wheel included for offline target-local installation'}
(ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
print(json.dumps({'files': len(files)}))

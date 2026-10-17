"""Portable, per-install paths; never accepts replacement ABI/version hashes."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def configuration():
    path = Path(os.environ.get('THS_READER_CONFIG', str(ROOT / 'reader-config.json'))).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Reader is not configured; run install.ps1 or create reader-config.json')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if value.get('schema_version') != 1 or not value.get('ths_root'):
        raise ValueError('Invalid reader configuration schema or missing ths_root')
    return value


def target(kind):
    profile = json.loads((ROOT / 'supported-profile.json').read_text(encoding='utf-8-sig'))
    return (Path(configuration()['ths_root']).expanduser().resolve() /
            profile['client_files'][kind]['path']).resolve()

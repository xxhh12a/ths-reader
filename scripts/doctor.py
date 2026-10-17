"""Offline compatibility checks only; does not attach to processes or query accounts."""
import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ths-root', type=Path)
    parser.add_argument('--config', type=Path, default=ROOT / 'reader-config.json')
    parser.add_argument('--skip-dependency', action='store_true')
    args = parser.parse_args()
    profile = json.loads((ROOT / 'supported-profile.json').read_text(encoding='utf-8-sig'))
    checks, errors = {}, []
    checks['python'] = {'version': list(sys.version_info[:3]), 'bits': struct.calcsize('P') * 8}
    if sys.platform != 'win32':
        errors.append('Windows is required')
    if sys.version_info[:2] < tuple(profile['python_min']) or checks['python']['bits'] != 64:
        errors.append('64-bit CPython 3.11+ is required')
    try:
        if args.ths_root:
            ths_root = args.ths_root.resolve()
        else:
            config = json.loads(args.config.read_text(encoding='utf-8-sig'))
            if config.get('schema_version') != 1:
                raise ValueError('Unsupported reader configuration')
            ths_root = Path(config['ths_root']).expanduser().resolve()
        for name, item in profile['client_files'].items():
            path = ths_root / item['path']
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            checks[name] = {'path': str(path), 'matches': actual == item['sha256'], 'actual_sha256': actual}
            if actual != item['sha256']:
                errors.append(name + ': unsupported or missing client file; do not bypass version checks')
    except Exception as error:
        errors.append('Configuration: ' + str(error))
    bridge = ROOT / profile['bridge']['path']
    checks['bridge'] = bridge.is_file() and hashlib.sha256(bridge.read_bytes()).hexdigest() == profile['bridge']['sha256']
    if not checks['bridge']:
        errors.append('Verified quote bridge is missing or changed')
    for name, item in profile.get('additional_bridges', {}).items():
        path = ROOT / item['path']
        checks[name] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        if not checks[name]:
            errors.append(name + ': verified bridge is missing or changed')
    if not args.skip_dependency:
        sys.path.insert(0, str(ROOT / 'native-work/python-deps'))
        try:
            import frida
            checks['frida_version'] = frida.__version__
            if frida.__version__ != profile['frida_version']:
                errors.append('Unexpected Frida version')
        except Exception as error:
            errors.append('Frida dependency: ' + str(error))
    result = {'status': 'compatible' if not errors else 'incompatible', 'checks': checks,
              'errors': errors, 'account_query_executed': False, 'login_verified': False}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())

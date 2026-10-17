"""Check the exact Git index, package hashes and common disclosure patterns offline."""
import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFIED_FRIDA_SHA256 = '7efdeb7e95c8e025517e203c2d1121c9eb07c9a3f33a1312ed5960c757e2fe98'
PATTERNS = {
    'windows_user_path': re.compile(rb'(?i)[a-z]:[\\/]Users[\\/][^\s"<>\x00]+'),
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github_token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    'api_key': re.compile(rb'\bsk-[A-Za-z0-9_-]{24,}'),
    'credential_url': re.compile(rb'https?://[^\s/:]+:[^\s/@]+@'),
    'account_reference': re.compile(rb'"account_ref"\s*:\s*"sha256:[a-f0-9]{64}"'),
}


def scan(label, raw, markers, findings, verified_upstream=False):
    for encoding in ('utf-8', 'utf-16-le'):
        decoded = raw.decode(encoding, errors='ignore').encode('utf-8')
        for name, pattern in PATTERNS.items():
            for hit in pattern.finditer(decoded):
                # These are build paths and URL-parser fixtures in the exact,
                # unmodified PyPI wheel, not author data or real credentials.
                if verified_upstream and name == 'windows_user_path' and re.match(
                        rb'(?i)[a-z]:[\\/]Users[\\/](runneradmin|\.\.\.)[\\/]', hit.group()):
                    continue
                if verified_upstream and name == 'credential_url' and hit.group() in {
                        b'https' + b'://abc:xyz@', b'https' + b'://a:b@',
                        b'https' + b'://abc:123@', b'https' + b'://123:xyz@',
                        b'http' + b'://abc:xyz@'}:
                    continue
                findings.append({'file': label, 'rule': name})
                break
        for marker in markers:
            if marker.encode('utf-8') in decoded:
                findings.append({'file': label, 'rule': 'local_deny_marker'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deny-file', type=Path, help='Local JSON string list; never publish it')
    args = parser.parse_args()
    markers = json.loads(args.deny_file.read_text(encoding='utf-8')) if args.deny_file else []
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    names = [name for name in names if name]
    if not names:
        raise ValueError('Stage the release files first')
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    expected = {item['path']: item for item in manifest['files']}
    if set(names) != set(expected) | {'manifest.json'}:
        raise ValueError('Git index and manifest paths differ')
    findings, archives, python_files = [], 0, 0
    for name in names:
        parts = Path(name).parts
        if (any(part in ('private', '.git', '__pycache__') for part in parts) or
                Path(name).name in ('reader-config.json', '.env') or
                Path(name).suffix.lower() in ('.log', '.pdb', '.dmp', '.pyc')):
            findings.append({'file': name, 'rule': 'runtime_or_private_file'})
        raw = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
        if name != 'manifest.json':
            item = expected[name]
            if len(raw) != item['size'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                findings.append({'file': name, 'rule': 'manifest_mismatch'})
        if raw != (ROOT / name).read_bytes():
            findings.append({'file': name, 'rule': 'index_differs_from_worktree'})
        scan(name, raw, markers, findings)
        if name.endswith('.py'):
            ast.parse(raw.decode('utf-8-sig'), filename=name)
            python_files += 1
        if name.endswith(('.whl', '.zip')):
            verified_upstream = hashlib.sha256(raw).hexdigest() == VERIFIED_FRIDA_SHA256
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if archive.testzip():
                    raise ValueError('Archive CRC failed: ' + name)
                for member in archive.infolist():
                    if not member.is_dir():
                        scan(name + '!' + member.filename, archive.read(member), markers, findings,
                             verified_upstream=verified_upstream)
            archives += 1
    result = {'status': 'passed' if not findings else 'failed', 'files': len(names),
              'python_syntax_checks': python_files, 'archives_expanded': archives,
              'findings': findings, 'live_account_query_executed': False}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())

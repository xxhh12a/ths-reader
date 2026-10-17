"""Version-bound xiadan account reader; native queries only, no screenshot/OCR."""
import argparse
import base64
import ctypes
import hashlib
import json
import sys
from ctypes import wintypes as W
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'native-work/python-deps'))
from reader_config import target
TARGET = target('account')
SHA = '08d7752ebd4019f80e3e6c5b7b40482e1d10f67a95590cdb5f470b586155d555'
QUERIES = {'asset': 'money', 'position': 'position', 'order': 'fullorder', 'deal': 'deal'}


def now():
    return datetime.now(timezone.utc).isoformat()


def decode(raw):
    for encoding in ('utf-8', 'gb18030'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError('Native reply encoding is not UTF-8 or GB18030')


def verify_process(pid):
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    k.QueryFullProcessImageNameW.restype = W.BOOL
    k.CloseHandle.argtypes = [W.HANDLE]
    k.CloseHandle.restype = W.BOOL
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name = ctypes.create_unicode_buffer(32768)
        size = W.DWORD(len(name))
        if not k.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if Path(name.value).resolve() != TARGET.resolve():
            raise ValueError('PID does not refer to the verified target')
    finally:
        k.CloseHandle(handle)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--pid', type=int, required=True)
    ap.add_argument('--capture', choices=[*QUERIES, 'snapshot'])
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError('Refuse overwriting an existing observation')
    if args.capture and not output.is_relative_to((ROOT / 'private').resolve()):
        raise ValueError('Account replies must stay in the ignored project private directory')
    if hashlib.sha256(TARGET.read_bytes()).hexdigest() != SHA:
        raise ValueError('Target version/hash changed; reverse engineering must be revalidated')
    verify_process(args.pid)
    import frida

    result = {'query_started_at': now(), 'source': 'xiadan.exe native queryClientData via Frida on main GUI thread',
              'target_sha256': SHA, 'frida_version': frida.__version__, 'source_timestamp': None,
              'status': 'preflight_only', 'queries': {}, 'limits': ['No source timestamp or upstream row count',
              'No verified broker refresh or atomic cross-query snapshot', 'Historical queries disabled after native exception']}
    packet, identity, errors = {}, {}, []
    session, script = None, None
    exit_code = 0
    try:
        session = frida.attach(args.pid)
        script = session.create_script((ROOT / 'native-probe/account_readonly.js').read_text(encoding='utf-8'))

        def on_message(message, data):
            try:
                if message.get('type') == 'error':
                    errors.append(message.get('description', 'Frida script error'))
                elif message.get('type') == 'send':
                    payload = message['payload']
                    raw = data or b''
                    if payload.get('type') == 'account_query_result':
                        text, encoding = decode(raw)
                        packet.update(raw_text=text, source_bytes_base64=base64.b64encode(raw).decode('ascii'),
                                      encoding=encoding, source_bytes_sha256=hashlib.sha256(raw).hexdigest(), native_metadata=payload)
                    elif payload.get('type') == 'account_identity':
                        identity.update(account_ref='sha256:' + hashlib.sha256(raw).hexdigest(),
                                        selection_stable=payload['selection_stable'])
            except Exception as exc:
                errors.append(str(exc))

        script.on('message', on_message)
        script.load()
        result['metadata'] = script.exports_sync.inspect()
        kinds = list(QUERIES) if args.capture == 'snapshot' else ([args.capture] if args.capture else [])
        for kind in kinds:
            packet.clear()
            identity.clear()
            observation = {'query_started_at': now(), 'query': kind, 'source_timestamp': None, 'status': 'querying'}
            result['queries'][kind] = observation
            try:
                observation['query_result'] = script.exports_sync.capture(kind)
                if errors or not packet or not identity:
                    raise RuntimeError('; '.join(errors) or 'Missing native reply/account reference')
                observation.update(identity)
                observation['raw_reply'] = dict(packet)
                reply = json.loads(packet['raw_text'])
                observation['reply'] = reply
                if reply.get('ok') is not True or reply.get('type') != kind:
                    raise RuntimeError('Native query returned an error or unexpected query type')
                table = reply.get('data', {}).get(QUERIES[kind])
                if not isinstance(table, dict):
                    raise RuntimeError('Missing expected native account table')
                if kind in ('order', 'deal'):
                    if not all(isinstance(rows, list) for rows in table.values()):
                        raise RuntimeError('Unexpected native detail row shape')
                    count = sum(len(rows) for rows in table.values())
                else:
                    count = len(table) if kind == 'position' else 1
                observation.update(status='success' if count else 'empty_return_unverified', row_count=count)
            except Exception as exc:
                observation.update(status='error', error=str(exc))
                raise
            finally:
                observation['captured_at'] = now()
        refs = {q['account_ref'] for q in result['queries'].values() if 'account_ref' in q}
        if len(refs) > 1:
            raise RuntimeError('Account changed between queries; do not combine observations')
        if refs:
            result['account_ref'] = next(iter(refs))
            result['account_selection_stable'] = True
        if kinds:
            result['status'] = 'native_queries_received'
        result['metadata_after'] = script.exports_sync.inspect()
    except Exception as exc:
        result.update(status='error', error=str(exc))
        exit_code = 1
    finally:
        result['captured_at'] = now()
        if script:
            try:
                script.unload()
            except Exception as exc:
                result['cleanup_error'] = str(exc)
                exit_code = 1
        if session:
            try:
                session.detach()
            except Exception as exc:
                result['detach_error'] = str(exc)
                exit_code = 1
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write('\n')
    print(json.dumps({'status': result['status'], 'output': str(output),
                      'queries': {q: {'status': v['status'], 'row_count': v.get('row_count')} for q, v in result['queries'].items()}}, ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())

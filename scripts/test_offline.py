"""Synthetic regression checks; never attaches to a client or reads real accounts."""
import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


class OfflineTests(unittest.TestCase):
    def test_empty_orders_and_deals_are_valid_observations(self):
        directory = ROOT / 'private' / ('synthetic-test-' + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        try:
            money = dict.fromkeys(['zjye', 'djje', 'kyje', 'kqje', 'zsz', 'zzc', 'jjzje'], '0.00')
            queries = {}
            for kind, table in [('asset', 'money'), ('position', 'position'),
                                ('order', 'fullorder'), ('deal', 'deal')]:
                reply = {'ok': True, 'type': kind, 'data': {table: money if kind == 'asset' else {}}}
                raw = json.dumps(reply).encode()
                queries[kind] = {'status': 'success' if kind == 'asset' else 'empty_return_unverified',
                                 'row_count': 1 if kind == 'asset' else 0,
                                 'selection_stable': True, 'account_ref': 'synthetic-only',
                                 'reply': reply, 'raw_reply': {'encoding': 'utf-8',
                                 'source_bytes_base64': base64.b64encode(raw).decode(),
                                 'source_bytes_sha256': hashlib.sha256(raw).hexdigest()}}
            snapshot = {'status': 'native_queries_received', 'captured_at': '2000-01-01T00:00:00+00:00',
                        'queries': queries}
            source, prefix = directory / 'input.json', directory / 'validation'
            source.write_text(json.dumps(snapshot), encoding='utf-8')
            command = [PYTHON, '-B', str(ROOT / 'native-probe/summarize_snapshot.py'),
                       '--input', str(source), '--output-prefix', str(prefix)]
            run = subprocess.run(command, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr.decode(errors='replace'))
            result = json.loads(prefix.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(result['row_counts'], {'positions': 0, 'orders': 0, 'deals': 0})
            self.assertIn('unverified', result['upstream_completeness'])
            queries['deal']['account_ref'] = 'different-synthetic-account'
            source.write_text(json.dumps(snapshot), encoding='utf-8')
            command[-1] = str(directory / 'rejected')
            rejected = subprocess.run(command, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse((directory / 'rejected.json').exists())
        finally:
            if directory.parent.resolve() != (ROOT / 'private').resolve():
                raise ValueError('Unexpected synthetic test directory')
            shutil.rmtree(directory)
            if not any(directory.parent.iterdir()):
                directory.parent.rmdir()

    def test_local_indicators_and_warmup(self):
        spec = importlib.util.spec_from_file_location('quote_validation', ROOT / 'native-probe/verify_quote_sample.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rows = [{'date_time': str(index), 'close': '2.00'} for index in range(60)]
        result = module.indicators(rows)
        self.assertEqual(result['source'], 'local_calculated')
        self.assertIsNone(result['series'][32]['MACD'])
        self.assertEqual(result['series'][33]['MACD']['histogram'], 0)
        self.assertEqual(result['latest']['MA']['60'], '2.00')

    def test_invalid_cli_input_stops_before_creating_output(self):
        for arguments in [('quote', '--pid', '1', '--code', 'invalid', '--market', '20'),
                          ('trend', '--pid', '1', '--code', '000001', '--market', '33', '--date', '2000-02-30')]:
            run = subprocess.run([PYTHON, '-B', str(ROOT / 'native-probe/read_ths.py'), *arguments],
                                 capture_output=True)
            self.assertEqual(run.returncode, 2)


if __name__ == '__main__':
    unittest.main()

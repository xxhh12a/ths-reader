"""On-demand CLI over the verified THS readers; never writes the finance ledger."""
import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBES = ROOT / 'native-probe'
MARKETS = {'17': 'USHA', '20': 'USHJ', '33': 'USZA', '36': 'USZJ'}
MARKETS.update({v: v for v in list(MARKETS.values())})


def now():
    return datetime.now(timezone.utc).isoformat()


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def source(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def worker(directory, label, script, args):
    # The canonical project owns execution and private output, regardless of caller CWD.
    run = subprocess.run([sys.executable, str(PROBES / script), *map(str, args)],
                         cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace')
    log = directory / (label + '-worker.log')
    with log.open('x', encoding='utf-8') as stream:
        stream.write(run.stdout)
        stream.write(run.stderr)
    if run.returncode:
        raise RuntimeError(f'{label} exited {run.returncode}; inspect {log}')


def account(args, directory, result):
    raw = directory / 'account-native.json'
    try:
        worker(directory, 'account', 'read_account_probe.py',
               ['--pid', args.pid, '--capture', 'snapshot', '--output', raw])
    finally:
        if raw.exists():
            result['sources'].append(source(raw))
    report = directory / 'account-validation'
    worker(directory, 'account-validation', 'summarize_snapshot.py',
           ['--input', raw, '--output-prefix', report])
    snapshot, validation = load(raw), load(report.with_suffix('.json'))
    result.update(account_ref=snapshot['account_ref'],
                  funds=validation['money_source_strings'],
                  valuation_status=validation['valuation_status'],
                  reconciliation=validation, captured_at=snapshot['captured_at'])
    fields = ['scdm', 'jysc', 'zqdm', 'zqmc', 'gpye', 'kyye', 'cbj', 'sj', 'sz', 'yk']
    result['positions'] = [{key: row.get(key) for key in fields}
                           for row in snapshot['queries']['position']['reply']['data']['position'].values()]
    result['reports'] = [str(report.with_suffix('.json')), str(report.with_suffix('.md'))]
    result['limits'].extend(['Account queries are not atomic; broker refresh and upstream completeness unverified',
                             'Cash and reverse repo bucket assignments remain unallocated',
                             'Reconciliation status is separate from successful acquisition'])
    result['status'] = 'collected'


def quote(args, directory, result):
    from verify_quote_sample import read, indicators
    market = MARKETS[args.market]
    result.update(code=args.code, market=market, adjustment='Exclude', periods={})
    result['limits'].extend(['Source refresh and cold-start behavior unverified; returned rows may be cached',
                             'Requested count does not reliably limit native returned rows',
                             'Final bar may belong to an unfinished day/week/month',
                             'MA/MACD are local_calculated; no UI comparison in this run',
                             'Cross-instrument volume/amount units are not fully verified'])
    bridge = ROOT / 'native-work/quote-bridge/query-general-v4/ThsQuoteBridge.dll'
    if hashlib.sha256(bridge.read_bytes()).hexdigest() != 'ba9ae2ad45821dc3c452b7671839c511f7a46b635605b4a4ab48470373cef758':
        raise ValueError('Verified quote bridge changed; revalidate it in the THS project')
    for period in args.periods:
        result['periods'][period] = {'status': 'not_attempted'}
    for period in args.periods:
        item = result['periods'][period]
        item['status'] = 'querying'
        raw = directory / (period + '-native.json')
        try:
            worker(directory, period, 'read_quote_probe.py',
                   ['--pid', args.pid, '--build', 'query-general-v4', '--code', args.code,
                    '--market', args.market, '--period', period, '--count', args.count, '--output', raw])
            reply, rows, raw_source = read(raw, args.code, market)
            if reply['period'] != period or not rows:
                raise ValueError('Wrong period or empty native sequence')
            # Use each period's full returned history, including for a single-period request.
            item.update(status='collected', native_metadata={k: v for k, v in reply.items() if k != 'responses'},
                        bars=rows, indicators=indicators(rows), last_data_date=rows[-1]['date_time'],
                        final_bar_completion='unverified')
            result['sources'].append(raw_source)
        except Exception:
            item['status'] = 'error'
            if raw.exists():
                result['sources'].append(source(raw))
                item['native_status'] = load(raw).get('status')
            raise
    result['status'] = 'collected'


def trend(args, directory, result):
    from verify_trend_sample import read
    market = MARKETS[args.market]
    date = args.date.replace('-', '')
    result.update(code=args.code, market=market, requested_date=args.date,
                  source_kind='software_native', subscription_created=False)
    bridge = ROOT / 'native-work/quote-bridge/trend-query-v5/ThsQuoteBridge.dll'
    if hashlib.sha256(bridge.read_bytes()).hexdigest() != '8c494bd5a0b54a6ba75f3c947720d299ae105dfceb8aae74d4375f93cb6762fd':
        raise ValueError('Verified trend bridge changed; revalidate it in the THS project')
    raw = directory / 'trend-native.json'
    try:
        worker(directory, 'trend', 'read_quote_probe.py',
               ['--pid', args.pid, '--build', 'trend-query-v5', '--code', args.code,
                '--market', args.market, '--trend-date', date, '--output', raw])
    finally:
        if raw.exists():
            result['sources'].append(source(raw))
    reply, rows, validation, _ = read(raw, args.code, market, date)
    result.update(status='collected', points=rows, validation=validation,
                  native_metadata={key: value for key, value in reply.items() if key != 'rows'},
                  first_data_time=rows[0]['date_time'], last_data_time=rows[-1]['date_time'],
                  source_timestamp=None, final_point_completion='unverified')
    result['limits'].extend(['One explicit trade date; validated semantic reference is 588220/USHJ on 2026-10-16',
                            'Source refresh, current-session freshness and upstream completeness remain unverified',
                            'Volume/amount are cumulative in native units; no cross-instrument unit guarantee',
                            'Missing/nonfinite native averages remain null; no derived VWAP',
                            'Time normalization uses this build/sample UTC clock convention; changed encoding fails validation',
                            'Managed helper remains loaded until application exit; no subscription is created'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    account_parser = commands.add_parser('account', help='Current funds, holdings and same-day orders/deals')
    account_parser.add_argument('--pid', type=int, required=True, help='Current verified xiadan.exe PID')
    quote_parser = commands.add_parser('quote', help='Native candles and local MA/MACD')
    quote_parser.add_argument('--pid', type=int, required=True, help='Current verified happ.exe PID')
    quote_parser.add_argument('--code', required=True)
    quote_parser.add_argument('--market', choices=MARKETS, required=True)
    quote_parser.add_argument('--periods', choices=['day', 'week', 'month'], nargs='+', default=['day', 'week', 'month'])
    quote_parser.add_argument('--count', type=int, default=200)
    trend_parser = commands.add_parser('trend', help='Native single-day intraday trend snapshot; no subscription')
    trend_parser.add_argument('--pid', type=int, required=True, help='Current verified happ.exe PID')
    trend_parser.add_argument('--code', required=True)
    trend_parser.add_argument('--market', choices=MARKETS, required=True)
    trend_parser.add_argument('--date', required=True, help='Explicit trade date YYYY-MM-DD')
    args = parser.parse_args()
    if args.pid <= 0:
        parser.error('PID must be positive')
    if args.operation in ('quote', 'trend'):
        if not (len(args.code) == 6 and args.code.isascii() and args.code.isdigit()):
            parser.error('Expected a six-digit security code')
    if args.operation == 'trend':
        try:
            if datetime.strptime(args.date, '%Y-%m-%d').strftime('%Y-%m-%d') != args.date:
                raise ValueError('Use YYYY-MM-DD')
        except ValueError:
            parser.error('Expected a valid explicit date YYYY-MM-DD')
    if args.operation == 'quote':
        if not 1 <= args.count <= 2000:
            parser.error('Count must be between 1 and 2000')
        if len(set(args.periods)) != len(args.periods):
            parser.error('Periods must be distinct')
    directory = ROOT / 'private' / (datetime.now().astimezone().strftime('%Y%m%d-%H%M%S-%f') +
                                    '-ths-' + args.operation + '-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    result = {'schema_version': 1, 'operation': args.operation, 'status': 'collecting',
              'started_at': now(), 'source_timestamp': None, 'read_only': True,
              'sources': [], 'limits': ['Native data acquisition; no Computer Use',
                                       'Collection time is not upstream source update time',
                                       'No ledger or finance STATUS update']}
    exit_code = 0
    try:
        {'account': account, 'quote': quote, 'trend': trend}[args.operation](args, directory, result)
    except Exception as error:
        result.update(status='partial_error' if result['sources'] else 'error', error=str(error))
        exit_code = 1
    finally:
        result['finished_at'] = now()
        result_path = directory / 'result.json'
        save(result_path, result)
    summary = {'status': result['status'], 'operation': args.operation, 'result': str(result_path)}
    if args.operation == 'account':
        summary['valuation_status'] = result.get('valuation_status')
        summary['position_count'] = len(result.get('positions', []))
    elif args.operation == 'trend':
        summary.update(point_count=len(result.get('points', [])), first_data_time=result.get('first_data_time'),
                       last_data_time=result.get('last_data_time'))
    else:
        summary['periods'] = {period: {'status': item['status'], 'rows': len(item.get('bars', [])),
                                      'last_data_date': item.get('last_data_date')}
                             for period, item in result.get('periods', {}).items()}
    if exit_code:
        summary['error'] = result['error']
    print(json.dumps(summary, ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())

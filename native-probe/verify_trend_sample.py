"""Validate native single-day trend rows; retain source values and missing fields."""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CN = timezone(timedelta(hours=8))
REQUIRED = ('latest', 'transaction_volume', 'transaction_amount')
TIME_BASIS = ('This version/sample uses UTC clock values with DateTime.Kind=Unspecified. '
              'UTC+8 verified against all 241 native HexinMinuteTime timestamps, closes and '
              'cumulative volume/amount for 588220 on 2026-10-16. '
              'Static parser evidence: TrendV4 UnixEpoch+milliseconds; minute bit layout.')


def number(field):
    if field.get('is_bad') or field.get('is_null') or field.get('value') is None:
        return None
    value = Decimal(field['value'])
    return value if value.is_finite() else None


def read(path, expected_code, expected_market, expected_date):
    raw = path.read_bytes()
    capture = json.loads(raw)
    reply = capture['observation']['reply']
    if capture['status'] != 'trend_rows_received' or reply['status'] != 'trend_rows_received':
        raise ValueError('No successful native trend response')
    if reply['is_bad'] or reply['subscription_created']:
        raise ValueError('Bad reply or unexpected subscription')
    if any(reply[key] != value for key, value in {
        'code': expected_code, 'reply_code': expected_code, 'market': expected_market,
        'reply_market': expected_market, 'requested_date': expected_date,
    }.items()):
        raise ValueError('Trend identity/date mismatch')
    if reply['request_range']['TradeDate'] != expected_date:
        raise ValueError('Request range mismatch')
    native_rows = reply['rows']
    if not native_rows or len(native_rows) != reply['row_count'] or len(native_rows) > 2000:
        raise ValueError('Empty/incorrect/budget-exceeding native row count')
    rows, previous, previous_volume, previous_amount = [], None, Decimal(0), Decimal(0)
    missing, gaps = {}, []
    for index, row in enumerate(native_rows):
        raw_time = datetime.fromisoformat(row['date_time'])
        if raw_time.tzinfo is not None or row['date_time_kind'] != 'Unspecified':
            raise ValueError('Source time encoding changed; revalidate normalization')
        time = raw_time.replace(tzinfo=timezone.utc).astimezone(CN)
        if time.strftime('%Y%m%d') != expected_date or time.second or time.microsecond:
            raise ValueError('Unexpected trade date or non-minute trend time')
        minute = time.hour * 60 + time.minute
        if not (570 <= minute <= 690 or 781 <= minute <= 900):
            raise ValueError('Unexpected Shanghai/Shenzhen session time')
        if previous is not None:
            if time <= previous:
                raise ValueError('Duplicate or unordered source times; refusing to sort')
            elapsed = (time - previous).total_seconds()
            if elapsed != 60 and not (previous.strftime('%H:%M') == '11:30' and time.strftime('%H:%M') == '13:01'):
                gaps.append({'after': previous.isoformat(), 'before': time.isoformat()})
        fields = row['fields']
        if fields.get('date_time', {}).get('value') != row['date_time']:
            raise ValueError('Time key/source field mismatch')
        values = {name: number(field) for name, field in fields.items() if name != 'date_time'}
        if any(values.get(name) is None for name in REQUIRED):
            raise ValueError('Missing/nonfinite required trend number')
        if values['latest'] <= 0 or values['transaction_volume'] < 0 or values['transaction_amount'] < 0:
            raise ValueError('Invalid price or negative volume/amount')
        if values['transaction_volume'] < previous_volume or values['transaction_amount'] < previous_amount:
            raise ValueError('Cumulative volume/amount decreased; semantics require revalidation')
        for name in reply['requested_fields']:
            if name != 'date_time' and values.get(name) is None:
                missing[name] = missing.get(name, 0) + 1
        rows.append({'date_time': time.isoformat(), 'raw_date_time': row['date_time'],
                     'price': str(values['latest']),
                     'cumulative_volume': str(values['transaction_volume']),
                     'cumulative_amount': str(values['transaction_amount']),
                     'prev_close': str(values['prev_close']) if values.get('prev_close') is not None else None,
                     # The candidate's semantics are not yet verified, even if a later sample is finite.
                     'native_average': None})
        previous, previous_volume, previous_amount = time, values['transaction_volume'], values['transaction_amount']
    validation = {'status': 'passed', 'checks': ['identity', 'requested_trade_date', 'strict_time_order',
                  'session_bounds', 'finite_positive_price', 'nonnegative_nondecreasing_cumulative_volume_amount',
                  'source_time_field_matches_key', 'no_subscription'],
                  'missing_or_nonfinite_fields': missing, 'gaps': gaps,
                  'observed_session_coverage': '09:30-11:30 and 13:01-15:00' if
                  len(rows) == 241 and not gaps and rows[0]['date_time'][11:16] == '09:30' and
                  rows[-1]['date_time'][11:16] == '15:00' else 'partial_or_unknown',
                  'upstream_completeness': 'unknown', 'time_normalization_basis': TIME_BASIS,
                  'semantic_reference_scope': '588220/USHJ, 2026-10-16; other samples not cross-validated',
                  'average_semantics': 'unverified; do not treat deal_average as a confirmed VWAP'}
    return reply, rows, validation, {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}


def minute_time(value):
    # Audited HexinMinuteTime::HexinTimeToUnixDateTime, token 0x06001ece.
    value = int(value)
    return datetime(1900 + ((value >> 20) & 255), (value >> 16) & 15,
                    (value >> 11) & 31, (value >> 6) & 31, value & 63, tzinfo=CN)


def compare_minutes(rows, path, code, market, date):
    raw = path.read_bytes()
    capture = json.loads(raw)
    reply = capture['observation']['reply']
    if capture['status'] != 'quote_rows_received' or reply['period'] != 'min1' or reply['period_real_id'] != 12288:
        raise ValueError('Expected audited one-minute reference')
    if reply['code'] != code or reply['market'] != market:
        raise ValueError('Reference security mismatch')
    bars = {}
    for response in reply['responses']:
        if response['is_bad'] or response['reply_security'] != reply['security']:
            raise ValueError('Bad minute reference')
        for source in response['rows']:
            time = minute_time(source['date_time']['value'])
            if time.strftime('%Y%m%d') != date:
                continue
            if time.isoformat() in bars:
                raise ValueError('Duplicate minute reference')
            bars[time.isoformat()] = source
    if set(bars) != {row['date_time'] for row in rows}:
        raise ValueError('Reference does not cover exactly the trend sample times')
    volume = amount = Decimal(0)
    differences = []
    for row in rows:
        bar = bars[row['date_time']]
        volume += Decimal(bar['transaction_volume']['value'])
        amount += Decimal(bar['transaction_amount']['value'])
        for field, actual, expected in (
            ('price_vs_minute_close', Decimal(row['price']), Decimal(bar['close']['value'])),
            ('cumulative_volume_vs_minute_sum', Decimal(row['cumulative_volume']), volume),
            ('cumulative_amount_vs_minute_sum', Decimal(row['cumulative_amount']), amount),
        ):
            if actual != expected:
                differences.append({'date_time': row['date_time'], 'field': field,
                                    'trend': str(actual), 'reference': str(expected)})
    return {'status': 'passed' if not differences else 'failed', 'matched_points': len(rows),
            'differences': differences, 'source': {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()},
            'volume_semantics': 'cumulative; exactly equals sum of native minute source volume',
            'amount_semantics': 'cumulative; exactly equals sum of native minute source amount',
            'quantity_units': 'same native units as minute source; no cross-instrument unit guarantee',
            'first_point': '09:30 present in both native sequences; counted once in cumulative comparison'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--expected-code', required=True)
    parser.add_argument('--expected-market', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--minute-reference', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'private') or output.exists():
        raise ValueError('Use a new private output path')
    reply, rows, validation, source = read(args.input, args.expected_code, args.expected_market, args.date)
    result = {'status': 'validated', 'source': source, 'validation': validation, 'rows': rows,
              'native_metadata': {key: value for key, value in reply.items() if key != 'rows'}}
    if args.minute_reference:
        result['minute_comparison'] = compare_minutes(rows, args.minute_reference, args.expected_code, args.expected_market, args.date)
        if result['minute_comparison']['status'] != 'passed':
            result['status'] = 'comparison_failed'
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'status': result['status'], 'rows': len(rows), 'output': str(output)}))
    return 0 if result['status'] == 'validated' else 1


if __name__ == '__main__':
    raise SystemExit(main())

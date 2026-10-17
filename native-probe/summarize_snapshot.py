"""Validate a saved native snapshot offline and write a private readable report."""
import argparse
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', required=True)
    ap.add_argument('--output-prefix', required=True)
    args = ap.parse_args()
    source, prefix = Path(args.input).resolve(), Path(args.output_prefix).resolve()
    private = (ROOT / 'private').resolve()
    if not source.is_relative_to(private) or not prefix.is_relative_to(private):
        raise ValueError('Input and output must stay in private/')
    snapshot = json.loads(source.read_text(encoding='utf-8'), object_pairs_hook=no_duplicates)
    if snapshot['status'] != 'native_queries_received':
        raise ValueError('Snapshot acquisition did not succeed')
    queries = snapshot['queries']
    if set(queries) != {'asset', 'position', 'order', 'deal'}:
        raise ValueError('Four account query observations are required')
    if len({q['account_ref'] for q in queries.values()}) != 1:
        raise ValueError('Account reference mismatch')
    for kind, q in queries.items():
        empty_table = (kind in ('position', 'order', 'deal') and
                       q['status'] == 'empty_return_unverified' and q.get('row_count') == 0)
        if (q['status'] != 'success' and not empty_table) or not q.get('selection_stable'):
            raise ValueError('Query/account selection not verified: ' + kind)
        raw = base64.b64decode(q['raw_reply']['source_bytes_base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != q['raw_reply']['source_bytes_sha256']:
            raise ValueError('Native byte digest mismatch: ' + kind)
        if json.loads(raw.decode(q['raw_reply']['encoding']), object_pairs_hook=no_duplicates) != q['reply']:
            raise ValueError('Saved reply differs from native bytes: ' + kind)
    money = queries['asset']['reply']['data']['money']
    positions = list(queries['position']['reply']['data']['position'].values())
    orders = [r for rows in queries['order']['reply']['data']['fullorder'].values() for r in rows]
    deals = [r for rows in queries['deal']['reply']['data']['deal'].values() for r in rows]
    position_keys = [(r['scdm'], r['zqdm']) for r in positions]
    deal_keys = [(r['cjrq'], r['scdm'], r['zqdm'], r['htbh'], r['cjbh']) for r in deals]
    if len(position_keys) != len(set(position_keys)) or len(deal_keys) != len(set(deal_keys)):
        raise ValueError('Duplicate security/deal identity in saved result')
    if not all(isinstance(v, str) for v in money.values()):
        raise ValueError('Unexpected numeric representation; preserve source strings')
    market_sum = sum(D(r['sz']) for r in positions)
    collateral = [r for r in positions if r['zqdm'] == '131990' and r['scdm'] == '1']
    # Only the instrument observed in this run; do not classify unknown instruments by name alone.
    repo_deals = [r for r in deals if r['zqdm'] == '131810' and r['scdm'] == '1']
    repo_principal = sum(D(r['cjje']) for r in repo_deals)
    collateral_value = sum(D(r['sz']) for r in collateral)
    residual = D(money['zzc']) - D(money['kyje']) - D(money['zsz'])
    checks = {'native_bytes_round_trip': True, 'same_account': True, 'unique_position_and_deal_keys': True,
              'position_sum_equals_money_zsz': market_sum == D(money['zsz']),
              'observed_repo_deal_amount_equals_collateral_value': bool(repo_deals) and repo_principal == collateral_value,
              'total_equals_available_plus_market': residual == 0}
    validation = {'source': str(source), 'checks': checks, 'row_counts': {'positions': len(positions), 'orders': len(orders), 'deals': len(deals)},
                  'money_source_strings': money, 'position_market_sum': str(market_sum), 'unexplained_asset_residual': str(residual),
                  'observed_repo_deals': repo_deals, 'observed_repo_principal': str(repo_principal),
                  'cash_bucket_assignment': 'unallocated', 'repo_bucket_assignment': 'unallocated',
                  'repo_maturity_date': None, 'repo_withdrawable_date': None, 'repo_interest_and_fees': None,
                  'upstream_completeness': 'unverified: serialized view has no source row count; position map keyed only by security code',
                  'valuation_status': 'reconciled' if residual == 0 and checks['position_sum_equals_money_zsz'] else 'partial_unreconciled',
                  'source_timestamp': None, 'atomic_snapshot': False, 'broker_refresh_verified': False}
    capture = datetime.fromisoformat(snapshot['captured_at']).astimezone(timezone(timedelta(hours=8))).isoformat()
    lines = ['# 同花顺原生查询结果', '', f'采集完成：{capture}（Asia/Shanghai）',
             f'原始文件：`{source.name}`。由 Frida 在 xiadan.exe 主线程调用原生查询函数取得；没有使用截图、OCR或可访问性文字。', '',
             '## 资金原值', '', '| 字段 | 含义 | 返回值（元） |', '|---|---|---:|']
    labels = {'zjye': '资金余额', 'djje': '冻结金额', 'kyje': '可用金额', 'kqje': '可取金额', 'zsz': '总市值', 'zzc': '总资产', 'jjzje': '基金总金额（沿用程序文档名称）'}
    lines += [f'| `{key}` | {label} | {money[key]} |' for key, label in labels.items()]
    lines += ['', '## 持仓原值', '', '| 市场 | 代码 | 名称 | 余额 | 可用数量 | 成本价 | 市价 | 市值 | 盈亏 |', '|---|---|---|---:|---:|---:|---:|---:|---:|']
    lines += [f"| {r['jysc']} | {r['zqdm']} | {r['zqmc']} | {r['gpye']} | {r['kyye']} | {r['cbj']} | {r['sj']} | {r['sz']} | {r['yk']} |" for r in positions]
    lines += ['', '## 当日成交原值', '', '| 日期 | 时间 | 代码 | 名称 | 操作 | 价格/利率源字段 | 数量 | 成交金额 |', '|---|---|---|---|---|---:|---:|---:|']
    lines += [f"| {r['cjrq']} | {r['cjsj']} | {r['zqdm']} | {r['zqmc']} | {r['cz']} | {r['cjjj']} | {r['cjsl']} | {r['cjje']} |" for r in deals]
    lines += ['', '## 逆回购与对账边界', '',
              f'本次 `131810 / R-001` 成交金额合计 {repo_principal} 元；`131990` 标准券市值 {collateral_value} 元。该标准券行已参加持仓市值合计 {market_sum} 元，不能再次把逆回购本金加到该市值上。',
              f'资金总资产减可用金额及总市值，尚有 {residual} 元差额。差额原因未从源字段查明，不自动归为分红、利息、逆回购或其他现金。',
              '现金及逆回购归仓均待划分。到期日、可取日、费用、利息和跨日续作尚未核实；不按零计。',
              '各查询有独立采集时间，但没有券商源更新时间，也未证实强制刷新。返回行数只表示本次序列化视图，不证明上游无分页或无被证券代码键覆盖的行。',
              '历史资金流水的空表尚未通过已有成交日验证；历史成交实验发生原生异常。正式读取器已移除历史调用，不把空表或异常当作没有历史交易。',
              '读取器未调用买卖、撤单、转账、策略或条件单接口。此处当日成交是软件返回的既有记录，不是探针执行的交易。', '']
    targets = {Path(str(prefix) + '.json'): json.dumps(validation, ensure_ascii=False, indent=2) + '\n',
               Path(str(prefix) + '.md'): '\n'.join(lines)}
    if any(path.exists() for path in targets):
        raise ValueError('Refuse overwriting an existing report')
    for path, content in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as f:
            f.write(content)
    print(json.dumps({'status': validation['valuation_status'], 'checks': checks,
                      'outputs': [str(p) for p in targets]}, ensure_ascii=False))


if __name__ == '__main__':
    main()

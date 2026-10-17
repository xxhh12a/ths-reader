"""Validate three native periods and calculate explicitly local MA/MACD series."""
import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime,timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
FIELDS=['date_time','open','high','low','close','transaction_volume','transaction_amount']

def read(path,expected_code='513010',expected_market=None):
    b=path.read_bytes();j=json.loads(b);reply=j['observation']['reply']
    if j['status']!='quote_rows_received' or reply['code']!=expected_code or reply['adjustment']!='Exclude':
        raise ValueError('Expected the received unadjusted sample')
    if expected_market is not None and reply['market']!=expected_market:raise ValueError('Wrong returned market')
    rows=[]
    for response in reply['responses']:
        if response['is_bad'] or response['reply_security']!=reply['security']:raise ValueError('Bad/mismatched response')
        rows.extend({k:r[k]['value'] for k in FIELDS} for r in response['rows'])
    if len(rows)!=reply['row_count']:raise ValueError('Native count mismatch')
    dates=[r['date_time'] for r in rows]
    if dates!=sorted(set(dates)):raise ValueError('Duplicate or unordered native dates')
    for r in rows:
        datetime.strptime(r['date_time'],'%Y%m%d')
        o,h,l,c=[Decimal(r[k]) for k in ['open','high','low','close']]
        if not l<=min(o,c)<=max(o,c)<=h:raise ValueError('OHLC bounds fail')
        if any(not Decimal(r[k]).is_finite() for k in FIELDS[1:]):raise ValueError('Nonfinite source number')
        if Decimal(r['transaction_volume'])<0 or Decimal(r['transaction_amount'])<0:raise ValueError('Negative volume/amount')
    return reply,rows,{'path':str(path),'sha256':hashlib.sha256(b).hexdigest()}

def key(date,period):
    d=datetime.strptime(date,'%Y%m%d')
    return tuple(d.isocalendar()[:2]) if period=='week' else (d.year,d.month)

def aggregate(rows,period):
    groups=defaultdict(list)
    for row in rows:groups[key(row['date_time'],period)].append(row)
    result={}
    for group,rs in groups.items():
        result[group]={'date_time':rs[-1]['date_time'],'open':rs[0]['open'],'close':rs[-1]['close'],
                       'high':str(max(Decimal(r['high']) for r in rs)),'low':str(min(Decimal(r['low']) for r in rs)),
                       'transaction_volume':str(sum(Decimal(r['transaction_volume']) for r in rs)),
                       'transaction_amount':str(sum(Decimal(r['transaction_amount']) for r in rs))}
    return result

def indicators(rows):
    closes=[Decimal(r['close']) for r in rows];fast=slow=float(closes[0]);dea=0.0;series=[]
    for i,c in enumerate(closes):
        if i:fast+=(float(c)-fast)*2/13;slow+=(float(c)-slow)*2/27
        dif=fast-slow
        if i:dea+=(dif-dea)*2/10
        series.append({'date_time':rows[i]['date_time'],
            'MA':{str(n):str(sum(closes[i-n+1:i+1])/n) if i+1>=n else None for n in [5,10,20,30,60]},
            'MACD':{'DIF':dif,'DEA':dea,'histogram':2*(dif-dea)} if i>=33 else None,
            'MACD_validity':'initialization_warmup' if i<33 else 'after_33_bar_warmup'})
    return {'source':'local_calculated','MA_parameters':[5,10,20,30,60],'MACD_parameters':[12,26,9],
            'formula':'EMA alpha=2/(N+1), first EMA=first close, first DEA=0; DIF=EMA12-EMA26; DEA=EMA9(DIF); histogram=2*(DIF-DEA)',
            'input_policy':'Full native returned history used before selecting the latest observations','series':series,'latest':series[-1]}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--input',action='append',required=True);ap.add_argument('--output-prefix',required=True);ap.add_argument('--ui-observations')
    ap.add_argument('--expected-code',default='513010');ap.add_argument('--expected-market',choices=['USHA','USHJ','USZA','USZJ']);a=ap.parse_args()
    prefix=Path(a.output_prefix).resolve()
    if not prefix.is_relative_to((ROOT/'private').resolve()):raise ValueError('Sample results must stay private')
    outputs=[prefix.with_suffix('.json'),prefix.with_suffix('.md')]
    if any(p.exists() for p in outputs):raise ValueError('Refuse overwriting observations')
    data={};sources=[]
    for path in a.input:
        reply,rows,source=read(Path(path).resolve(),a.expected_code,a.expected_market);period=reply['period']
        if data and reply['security']!=next(iter(data.values()))['native_metadata']['security']:raise ValueError('Mixed securities in period inputs')
        if period in data:raise ValueError('Duplicate period input')
        data[period]={'native_metadata':{k:v for k,v in reply.items() if k!='responses'},'bars':rows,'indicators':indicators(rows)};sources.append(source)
    if set(data)!={'day','week','month'}:raise ValueError('All three native periods required')
    comparisons={}
    for period in ['week','month']:
        derived=aggregate(data['day']['bars'],period);diffs=[]
        for r in data[period]['bars']:
            expected=derived.get(key(r['date_time'],period))
            if expected is None:diffs.append({'date_time':r['date_time'],'error':'No daily coverage'});continue
            for f in FIELDS:
                if (r[f]!=expected[f] if f=='date_time' else Decimal(r[f])!=Decimal(expected[f])):
                    diffs.append({'date_time':r['date_time'],'field':f,'native':r[f],'daily_aggregate':expected[f]})
        stats={}
        for f in FIELDS:
            ds=[d for d in diffs if d.get('field')==f]
            stats[f]={'difference_count':len(ds)}
            if ds and f!='date_time':
                stats[f].update(max_absolute_difference=str(max(abs(Decimal(d['native'])-Decimal(d['daily_aggregate'])) for d in ds)),
                    max_relative_difference=str(max(abs(Decimal(d['native'])-Decimal(d['daily_aggregate']))/abs(Decimal(d['daily_aggregate'])) for d in ds if Decimal(d['daily_aggregate']))))
        comparisons[period]={'native_bar_count':len(data[period]['bars']),'daily_group_count':len(derived),'exact_difference_count':len(diffs),'field_statistics':stats,'differences':diffs}
    ui_check={'status':'not_compared_in_this_run'}
    if a.ui_observations:
        path=Path(a.ui_observations).resolve();raw=path.read_bytes();ui=json.loads(raw);checks=[]
        if ui['code']!=a.expected_code or ui['chart_adjustment']!='除权':raise ValueError('UI reference scope mismatch')
        for p,expected in ui['periods'].items():
            latest=data[p]['indicators']['latest']
            for indicator in ['MA','MACD']:
                for name,value in expected[indicator].items():
                    got=Decimal(str(latest[indicator][name])).quantize(Decimal('0.001'),rounding=ROUND_HALF_UP)
                    checks.append({'period':p,'indicator':indicator,'component':name,'computed_rounded':str(got),'ui_observed':value,'match':got==Decimal(value)})
        ui_check={'status':'latest_display_precision_matches' if all(c['match'] for c in checks) else 'mismatch',
                  'reference':str(path),'reference_sha256':hashlib.sha256(raw).hexdigest(),'checks':checks,
                  'limit':'One saved observation of latest values; not current live verification or per-bar native equality'}
    result={'created_at':datetime.now(timezone.utc).isoformat(),'status':'three_native_periods_received_indicators_local',
            'code':a.expected_code,'sources':sources,'periods':data,'native_vs_daily_aggregation':comparisons,'ui_indicator_comparison':ui_check,
            'limits':['Source update time and forced upstream refresh are unverified',
                      'Requested count does not restrict the returned full sequence in this observed path',
                      'The current calendar month is incomplete; the final native date is the latest included trading day',
                      'No original network packet was captured; native parsed values and acquisition JSON bytes are retained',
                      'MA/MACD are local calculations; software display comparisons are separate evidence',
                      'Volume and amount source fields are retained; general cross-instrument unit rules remain unverified']}
    text=[f'# {a.expected_code} 三周期 K 线与本地指标验证','',
          'K 线来自软件 RequestCandleV2；MA、MACD 为明确标记的本地计算。原件及采集时点见同名 JSON。','',
          '| 周期 | 实际条数 | 首日 | 末日 | MA5 | DIF | DEA | MACD柱 |',
          '|---|---:|---|---|---:|---:|---:|---:|']
    for p in ['day','week','month']:
        v=data[p];bars=v['bars'];latest=v['indicators']['latest'];macd=latest['MACD']
        text.append(f"| {p} | {len(bars)} | {bars[0]['date_time']} | {bars[-1]['date_time']} | {latest['MA']['5']} | {macd['DIF']:.9f} | {macd['DEA']:.9f} | {macd['histogram']:.9f} |")
    text+=['','## 独立一致性核对','']
    for p,c in comparisons.items():text.append(f"- {p}：原生 {c['native_bar_count']} 根，对应日线聚合 {c['daily_group_count']} 组；字段逐项精确差异 {c['exact_difference_count']} 处，明细保存在 JSON。")
    text+=['',f"MA/MACD 界面观察核对：`{ui_check['status']}`。未提供同标的观察时，不声称已核对软件显示值。"]
    text+=['','## 口径与边界','',
           '请求口径为软件 RightStatus.Exclude（除权/不复权）。每周期使用自己的完整返回序列计算 MA(5/10/20/30/60) 和 MACD(12/26/9)，MACD柱为 2×(DIF−DEA)。前 33 根 MACD 标记预热，不作为已核验值。',
           '',f"各周期末日：{', '.join(p+'='+v['bars'][-1]['date_time'] for p,v in data.items())}。采集时间不等于行情源更新时间；未结束周期不可标为完整周期。本地指标不宣称逐根等同原生指标。",
           '','本轮保留源字符串、源字段与数值类型的采集原件；未捕获原始网络报文。范围参数、刷新和跨品种单位仍待验收。']
    with outputs[0].open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    outputs[1].write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(json.dumps({'outputs':[str(p) for p in outputs],'bars':{p:len(v['bars']) for p,v in data.items()},'aggregation_differences':{p:c['exact_difference_count'] for p,c in comparisons.items()},'ui_comparison':ui_check['status']},ensure_ascii=False))

if __name__=='__main__':main()

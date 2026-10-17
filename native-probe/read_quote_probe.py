"""Version-bound bridge into the current happ.exe .NET quote session."""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'native-work/python-deps'))
import frida
from reader_config import target
TARGET=target('quote')
SHA='aadcc88f35af1a5de83bcf181664ec12dd0116fc343064123cbbde0b1a756978'
JS=r"""
'use strict';
if(Process.arch!=='ia32'||Process.mainModule.path.toLowerCase()!==EXPECTED_TARGET_PATH)throw Error('Wrong process/architecture');
const host=Process.getModuleByName('hostfxr.dll');
const getRuntime=new NativeFunction(host.getExportByName('hostfxr_get_runtime_delegate'),'int',['pointer','int','pointer'],{abi:'mscdecl',scheduling:'cooperative',exceptions:'steal'});
rpc.exports={run(assembly,config,payload){
 const init=new NativeFunction(host.getExportByName('hostfxr_initialize_for_runtime_config'),'int',['pointer','pointer','pointer'],{abi:'mscdecl',scheduling:'cooperative',exceptions:'steal'});
 const close=new NativeFunction(host.getExportByName('hostfxr_close'),'int',['pointer'],{abi:'mscdecl',scheduling:'cooperative',exceptions:'steal'});
 const handleStorage=Memory.alloc(4);handleStorage.writePointer(ptr(0));
 const initCode=init(Memory.allocUtf16String(config),ptr(0),handleStorage);
 const handle=handleStorage.readPointer();
 if(initCode!==1){if(!handle.isNull())close(handle);throw Error('Expected already-initialized compatible runtime, got 0x'+(initCode>>>0).toString(16));}
 const ptrToLoader=Memory.alloc(4);ptrToLoader.writePointer(ptr(0));
 const rc=getRuntime(handle,5,ptrToLoader);
 const closeCode=close(handle);if(closeCode!==0)throw Error('Could not close owned secondary host context');
 if(rc!==0)throw Error('hostfxr_get_runtime_delegate failed: 0x'+(rc>>>0).toString(16));
 const loader=ptrToLoader.readPointer();if(loader.isNull())throw Error('Null host loader');
 const load=new NativeFunction(loader,'int',['pointer','pointer','pointer','pointer','pointer','pointer'],{abi:'stdcall',scheduling:'cooperative',exceptions:'steal'});
 const ptrToEntry=Memory.alloc(4);ptrToEntry.writePointer(ptr(0));
 const loaded=load(Memory.allocUtf16String(assembly),Memory.allocUtf16String('ThsQuoteBridge.EntryPoint, ThsQuoteBridge'),Memory.allocUtf16String('Run'),ptr(0),ptr(0),ptrToEntry);
 if(loaded!==0)throw Error('Managed bridge load failed: 0x'+(loaded>>>0).toString(16));
 const entry=ptrToEntry.readPointer();if(entry.isNull())throw Error('Null managed entry');
 const invoke=new NativeFunction(entry,'int',['pointer','int'],{abi:'stdcall',scheduling:'cooperative',exceptions:'steal'});
 const data=Memory.allocUtf8String(JSON.stringify(payload)),output=Memory.alloc(8*1024*1024),args=Memory.alloc(20);
 args.writeS32(0);args.add(4).writeS32(8*1024*1024);args.add(8).writePointer(output);args.add(12).writeS32(JSON.stringify(payload).length);args.add(16).writePointer(data);
 const returned=invoke(args,20),length=args.readS32();
 if(returned!==0||length<=0||length>8*1024*1024)throw Error('Managed result failure: '+returned+' length='+length);
 return {status:'managed_reply_received',pid:Process.id,hostfxr_path:host.path,secondary_host_init_code:initCode,secondary_host_close_code:closeCode,reply:JSON.parse(output.readUtf8String(length)),bytes:length};
}};
"""

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--pid',type=int,required=True);ap.add_argument('--output',required=True)
 ap.add_argument('--period',choices=['day','week','month','min1']);ap.add_argument('--count',type=int,default=200)
 ap.add_argument('--inspect-trend',action='store_true')
 ap.add_argument('--trend-date',help='Explicit single trade date, YYYYMMDD; independent trend request')
 ap.add_argument('--code',default='513010');ap.add_argument('--market',choices=['17','20','33','36','USHA','USHJ','USZA','USZJ'],default='20')
 ap.add_argument('--build',default='query-general-v4');a=ap.parse_args()
 if len(a.code)!=6 or not a.code.isascii() or not a.code.isdigit():raise ValueError('Expected a six-digit security code')
 if sum([bool(a.period),a.inspect_trend,bool(a.trend_date)])>1:raise ValueError('Choose only one operation')
 if a.trend_date:
  if len(a.trend_date)!=8 or datetime.strptime(a.trend_date,'%Y%m%d').strftime('%Y%m%d')!=a.trend_date:raise ValueError('Expected YYYYMMDD')
 if a.period=='min1' and not a.build.startswith('trend-query-'):raise ValueError('min1 is an experimental trend-reference path only; not part of the stable quote CLI')
 if a.build=='query-worker-v2' and (a.code!='513010' or a.market!='20'):raise ValueError('Use a general quote build for other securities/markets')
 if not a.build.replace('-','').isalnum():raise ValueError('Invalid build name')
 out=Path(a.output).resolve();bridge=ROOT/'native-work/quote-bridge'/a.build/'ThsQuoteBridge.dll'
 if not out.is_relative_to((ROOT/'private').resolve()) or out.exists():raise ValueError('Use a new private output path')
 if hashlib.sha256(TARGET.read_bytes()).hexdigest()!=SHA:raise ValueError('Target version changed')
 if a.trend_date or a.inspect_trend:
  audited_modules={'Hevo.Api.Quotes.dll':'a2068f022ef1e6cdf1e228cbde7ccbb85f21810f9121a18437162bb2a42379fe',
   'Hevo.Core.DataModel.dll':'8ad2e78c998c0b3207eef01f61392f36083606f8c5ee7e04fc2eab16e4f04532',
   'Hevo.Core.Interfaces.dll':'3f0a63025f4b93eabda5adee9d9e5a6f06caa6021a2568df8bb00833d1106993',
   'Hevo.MarketData.dll':'19e70973cd7361ef80078fbf307aa70b401f67b7d7c31aa50e2a228372a24bd1'}
  for name,expected in audited_modules.items():
   if hashlib.sha256((TARGET.parent/name).read_bytes()).hexdigest()!=expected:raise ValueError('Audited trend module changed: '+name)
 result={'started_at':datetime.now(timezone.utc).isoformat(),'target_sha256':SHA,'bridge_sha256':hashlib.sha256(bridge.read_bytes()).hexdigest(),
         'source':'happ existing .NET runtime via hostfxr; external managed bridge','operation':'query' if a.period else 'inspect','limits':['Managed bridge remains loaded until client exits','Frida detach does not unload the managed bridge','Query wait timeout does not cancel the underlying request']}
 session=script=None;code=0
 try:
  payload={'operation':'query','code':a.code,'market':a.market,'period':a.period,'count':a.count} if a.period else {'operation':'inspect_trend' if a.inspect_trend else 'inspect'}
  if a.trend_date:payload={'operation':'query_trend','code':a.code,'market':a.market,'date':a.trend_date}
  result['operation']=payload['operation']
  session=frida.attach(a.pid);script=session.create_script('const EXPECTED_TARGET_PATH='+json.dumps(str(TARGET).lower())+';\n'+JS);script.load();result['observation']=script.exports_sync.run(str(bridge),str(ROOT/'native-probe/QuoteBridge.runtimeconfig.json'),payload);result['status']=result['observation']['reply']['status']
  if a.period or a.trend_date:
   result['start_observation']=result['observation']
   deadline=time.monotonic()+40
   while result['status'] in ['query_started_on_managed_worker','query_pending'] and time.monotonic()<deadline:
    time.sleep(0.25)
    result['observation']=script.exports_sync.run(str(bridge),str(ROOT/'native-probe/QuoteBridge.runtimeconfig.json'),{'operation':'poll'})
    result['status']=result['observation']['reply']['status']
   if result['status'] in ['query_started_on_managed_worker','query_pending']:result['status']='host_poll_timeout_request_may_continue';code=1
  if result['status'] not in ['managed_preflight_only','managed_trend_preflight_only','quote_rows_received','trend_rows_received']:code=1
 except Exception as ex:result.update(status='error',error=str(ex));code=1
 finally:
  if script:
   try:script.unload()
   except Exception as ex:result['cleanup_error']=str(ex);code=1
  if session:
   try:session.detach()
   except Exception as ex:result['detach_error']=str(ex);code=1
  result['finished_at']=datetime.now(timezone.utc).isoformat()
  with out.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
 print(json.dumps({'status':result['status'],'output':str(out),'error':result.get('error')},ensure_ascii=False));return code
if __name__=='__main__':sys.exit(main())

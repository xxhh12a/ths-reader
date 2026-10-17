// In-process quote queries only. Loaded through the existing hostfxr runtime.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Globalization;

namespace ThsQuoteBridge
{
    public static class EntryPoint
    {
        static readonly object Sync=new object();
        static Task<object> Pending;
        static Type Find(string name)
        {
            var matches=AppDomain.CurrentDomain.GetAssemblies().Select(a=>a.GetType(name,false)).Where(t=>t!=null).ToArray();
            if(matches.Length!=1)throw new InvalidOperationException("Expected one loaded type: "+name+"; found "+matches.Length);
            return matches[0];
        }
        static object Get(object obj,string name) => obj.GetType().GetProperty(name,BindingFlags.Public|BindingFlags.Instance).GetValue(obj);
        static object Static(Type t,string name)
        {
            var p=t.GetProperty(name,BindingFlags.Public|BindingFlags.Static);
            if(p!=null)return p.GetValue(null);
            var f=t.GetField(name,BindingFlags.Public|BindingFlags.Static);
            if(f!=null)return f.GetValue(null);
            throw new MissingMemberException(t.FullName,name);
        }
        static object Describe(Type t)=>new {
            type=t.FullName,assembly=t.Assembly.GetName().Name,
            methods=t.GetMethods(BindingFlags.Public|BindingFlags.Instance|BindingFlags.Static|BindingFlags.DeclaredOnly)
                .Where(m=>m.Name=="RequestCandleV2"||m.Name=="RequestCandleV1"||m.Name=="get_Current"||t.Name=="DataResponseV2")
                .Select(m=>new {name=m.Name,token=m.MetadataToken,returns=m.ReturnType.ToString(),parameters=m.GetParameters().Select(p=>new {name=p.Name,type=p.ParameterType.ToString()})}),
            fields=t.GetFields(BindingFlags.Public|BindingFlags.Static).Select(f=>new {name=f.Name,type=f.FieldType.FullName})
        };
        static object Inspect()
        {
            return new {status="managed_preflight_only",native_thread=System.Threading.Thread.CurrentThread.ManagedThreadId,
                types=new[]{"Hevo.Core.DataCenter","Hevo.Core.DataCenterExtension","Hevo.Core.Periods","Hevo.Core.Security","Hevo.Core.Protocols.DataResponseV2"}.Select(n=>Describe(Find(n))),
                query_executed=false};
        }
        static object Query(JsonElement request)
        {
            string code=request.GetProperty("code").GetString(),market=request.GetProperty("market").GetString();
            string periodName=request.GetProperty("period").GetString();
            int count=request.GetProperty("count").GetInt32();
            if(code==null||code.Length!=6||!code.All(c=>c>='0'&&c<='9')||
                !new[]{"17","20","33","36","USHA","USHJ","USZA","USZJ"}.Contains(market)||
                !new[]{"day","week","month","min1"}.Contains(periodName)||count<1||count>2000)
                throw new InvalidOperationException("Expected a six-digit Shanghai/Shenzhen code and an enabled period");
            Type dc=Find("Hevo.Core.DataCenter"),extensions=Find("Hevo.Core.DataCenterExtension");
            if(extensions.Module.ModuleVersionId!=Guid.Parse("43e5a11b-557e-4dbd-94cf-872ca400cbed"))
                throw new InvalidOperationException("Quotes module MVID changed");
            object current=Static(dc,"Current");
            if(current==null)throw new InvalidOperationException("No active DataCenter");
            Type securityType=Find("Hevo.Core.Security");
            object security=Activator.CreateInstance(securityType,new object[]{market,code});
            bool bad=(bool)securityType.GetMethod("IsNullOrBad",new[]{securityType}).Invoke(null,new[]{security});
            if(bad||Convert.ToString(Get(security,"Code"),CultureInfo.InvariantCulture)!=code)
                throw new InvalidOperationException("Invalid or mismatched sample security");
            string resolvedMarket=Convert.ToString(Get(security,"Market"),CultureInfo.InvariantCulture);
            var marketAliases=new Dictionary<string,string>{{"17","USHA"},{"20","USHJ"},{"33","USZA"},{"36","USZJ"}};
            string expectedMarket=marketAliases.TryGetValue(market,out var alias)?alias:market;
            if(resolvedMarket!=expectedMarket)
                throw new InvalidOperationException("Resolved security market does not match the request");
            object period=Static(Find("Hevo.Core.Periods"),periodName=="min1"?"one_min":periodName);
            Type fieldType=Find("Hevo.Core.QuoteField"),fieldsType=Find("Hevo.DataModel.Business.QuoteFields");
            string[] fieldNames={"date_time","open","high","low","close","transaction_volume","transaction_amount"};
            Array fields=Array.CreateInstance(fieldType,fieldNames.Length);
            var fieldMap=new Dictionary<object,string>();
            for(int i=0;i<fieldNames.Length;i++){object field=Static(fieldsType,fieldNames[i]);fields.SetValue(field,i);fieldMap.Add(field,fieldNames[i]);}
            object rights=Activator.CreateInstance(Find("Hevo.Core.RightArgs"));
            rights.GetType().GetProperty("Status").SetValue(rights,Enum.Parse(Find("Hevo.Core.RightStatus"),"Exclude"));
            rights.GetType().GetProperty("IsVolumeExclude").SetValue(rights,false);
            MethodInfo method=(MethodInfo)extensions.Module.ResolveMethod(0x060016f1);
            if(method.Name!="RequestCandleV2"||method.GetParameters().Length!=9)
                throw new InvalidOperationException("Audited query signature changed");
            var started=DateTimeOffset.UtcNow;
            Task task=(Task)method.Invoke(null,new object[]{current,security,period,-(long)count,"0",fields,rights,"",null});
            if(!task.Wait(TimeSpan.FromSeconds(30)))return new {status="query_wait_timeout",source_timestamp=(string)null,
                query_started_at=started,request_may_still_complete=true};
            var responses=new List<object>();int rowsCount=0,enumeratedBytes=0;bool anyBad=false;
            foreach(object response in (IEnumerable)task.GetType().GetProperty("Result").GetValue(task))
            {
                bool responseBad=(bool)Get(response,"IsBad");anyBad|=responseBad;
                var rows=new List<Dictionary<string,object>>();
                FieldInfo rawField=response.GetType().Module.ResolveField(0x04002136);
                if(rawField.DeclaringType!=response.GetType()||rawField.FieldType!=typeof(byte[]))
                    throw new InvalidOperationException("Audited response buffer field changed");
                byte[] raw=(byte[])rawField.GetValue(response);
                if(raw!=null&&raw.Length>2*1024*1024)throw new InvalidOperationException("Response exceeds sample byte budget");
                int availableSourceRows=0;
                if(!responseBad)
                {
                    IList parsed=(IList)response.GetType().GetMethod("AsListDictionary",Type.EmptyTypes).Invoke(response,null);
                    availableSourceRows=parsed.Count;
                    IEnumerable<IDictionary> selected=parsed.Cast<IDictionary>();
                    if(periodName=="min1")selected=selected.Skip(Math.Max(0,parsed.Count-count));
                    foreach(IDictionary row in selected)
                    {
                        if(rowsCount+rows.Count>=10000)throw new InvalidOperationException("Candle enumeration row budget exceeded");
                        var values=new Dictionary<string,object>();
                        foreach(DictionaryEntry pair in row)
                        {
                            string name=fieldMap.TryGetValue(pair.Key,out var mapped)?mapped:"field:"+Convert.ToString(pair.Key,CultureInfo.InvariantCulture);
                            values.Add(name,new {value=pair.Value==null?null:Convert.ToString(pair.Value,CultureInfo.InvariantCulture),
                                source_type=pair.Value==null?null:pair.Value.GetType().FullName,source_field=Convert.ToString(pair.Key,CultureInfo.InvariantCulture)});
                            enumeratedBytes+=Encoding.UTF8.GetByteCount(pair.Value==null?"":Convert.ToString(pair.Value,CultureInfo.InvariantCulture))+160;
                            if(enumeratedBytes>4*1024*1024)throw new InvalidOperationException("Candle enumeration byte budget exceeded");
                        }
                        rows.Add(values);
                    }
                }
                // This response can expose parsed rows while AsBytes rejects its compression tag.
                // Keep the stored response buffer separately; do not call it a wire packet.
                rowsCount+=rows.Count;
                responses.Add(new {is_bad=responseBad,instance_id=Get(response,"InstanceID"),
                    available_source_rows=availableSourceRows,selection=periodName=="min1"?"explicit local tail selection for trend comparison; not complete native history":"all parsed rows",
                    reply_security=Convert.ToString(Get(response,"requestSecurity"),CultureInfo.InvariantCulture),
                    is_binary=Get(response,"IsBinary"),is_compressed=Get(response,"IsCompress"),
                    raw_base64=raw==null?null:Convert.ToBase64String(raw),raw_source="stored DataResponseV2 byte array; not claimed as original wire packet",rows});
            }
            return new {status=anyBad?"error_response":rowsCount>0?"quote_rows_received":"empty_return_unverified",code,requested_market=market,
                security=security.ToString(),market=Convert.ToString(Get(security,"Market"),CultureInfo.InvariantCulture),period=periodName,
                period_real_id=period.GetType().GetMethod("GetRealId").Invoke(period,null),requested_count=count,
                adjustment="Exclude",volume_exclude=false,source_timestamp=(string)null,query_started_at=started,captured_at=DateTimeOffset.UtcNow,
                row_count=rowsCount,responses,source="software RequestCandleV2 and AsListDictionary"};
        }
        public static int Run(IntPtr args,int sizeBytes)
        {
            if(sizeBytes!=20||IntPtr.Size!=4)return -10;
            int capacity=Marshal.ReadInt32(args,4),inputLength=Marshal.ReadInt32(args,12);
            IntPtr output=Marshal.ReadIntPtr(args,8),input=Marshal.ReadIntPtr(args,16);
            if(capacity<=0||capacity>8*1024*1024||inputLength<=0||inputLength>8192)return -11;
            object result;
            try
            {
                using(var doc=JsonDocument.Parse(Marshal.PtrToStringUTF8(input,inputLength)))
                {
                    string operation=doc.RootElement.GetProperty("operation").GetString();
                    if(operation=="inspect")result=Inspect();
                    else if(operation=="inspect_trend")result=TrendReader.Inspect();
                    else if(operation=="query"||operation=="query_trend")
                    {
                        var copy=doc.RootElement.Clone();
                        lock(Sync)
                        {
                            if(Pending!=null&&!Pending.IsCompleted)throw new InvalidOperationException("A query is already pending");
                            Pending=Task.Run<object>(()=>{
                                try{return operation=="query_trend"?TrendReader.Query(copy):Query(copy);}
                                catch(Exception error)
                                {
                                    if(error is TargetInvocationException&&error.InnerException!=null)error=error.InnerException;
                                    if(error is AggregateException)error=((AggregateException)error).Flatten().InnerExceptions[0];
                                    return new {status="error",error_type=error.GetType().FullName,error=error.Message,stack=error.StackTrace};
                                }
                            });
                        }
                        result=new {status="query_started_on_managed_worker"};
                    }
                    else if(operation=="poll")
                    {
                        lock(Sync)
                        {
                            if(Pending==null)throw new InvalidOperationException("No query has been started");
                            result=Pending.IsCompleted?Pending.GetAwaiter().GetResult():new {status="query_pending"};
                        }
                    }
                    else throw new InvalidOperationException("Unsupported operation");
                }
            }
            catch(Exception ex)
            {
                if(ex is TargetInvocationException && ex.InnerException!=null)ex=ex.InnerException;
                result=new {status="error",error_type=ex.GetType().FullName,error=ex.Message};
            }
            byte[] bytes=JsonSerializer.SerializeToUtf8Bytes(result,new JsonSerializerOptions{WriteIndented=true});
            if(bytes.Length>capacity){Marshal.WriteInt32(args,0,-bytes.Length);return -12;}
            Marshal.Copy(bytes,0,output,bytes.Length);Marshal.WriteInt32(args,0,bytes.Length);return 0;
        }
    }
}

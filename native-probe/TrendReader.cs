// Version-bound finite trend queries; deliberately does not subscribe to pushes.
using System;
using System.Linq;
using System.Reflection;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace ThsQuoteBridge
{
    public static class TrendReader
    {
        static Task NativePending;
        static object Get(object obj,string name)
        {
            if(obj==null)throw new InvalidOperationException("Null object while reading "+name);
            var property=obj.GetType().GetProperty(name);
            if(property==null)throw new MissingMemberException(obj.GetType().FullName,name);
            return property.GetValue(obj);
        }
        static object Via(object obj,string type,string name) => Find(type).GetProperty(name).GetValue(obj);
        static object Static(string type,string name) => Find(type).GetProperty(name,BindingFlags.Public|BindingFlags.Static).GetValue(null);
        static Type Find(string name)
        {
            var types=AppDomain.CurrentDomain.GetAssemblies().Select(a=>a.GetType(name,false)).Where(t=>t!=null).ToArray();
            if(types.Length!=1)throw new InvalidOperationException("Expected one loaded type: "+name);
            return types[0];
        }
        static object Describe(Type t) => new {
            type=t.FullName,assembly=t.Assembly.GetName().Name,mvid=t.Module.ModuleVersionId,
            interfaces=t.GetInterfaces().Select(i=>i.FullName),
            methods=t.GetMethods(BindingFlags.Public|BindingFlags.Instance|BindingFlags.Static)
                .Select(m=>new {name=m.Name,token=m.MetadataToken,returns=m.ReturnType.ToString(),parameters=m.GetParameters().Select(p=>new{name=p.Name,type=p.ParameterType.ToString()})}),
            properties=t.GetProperties().Select(p=>new{name=p.Name,type=p.PropertyType.ToString()}),
            fields=t.GetFields(BindingFlags.Public|BindingFlags.Instance).Select(f=>new{name=f.Name,type=f.FieldType.ToString()})
        };
        public static object Inspect()
        {
            string[] names={"Hevo.Core.Interfaces.Model.ITrendQuotePamameters","Hevo.Core.Interfaces.Interface.Reponse.ITrendReplyData",
                "Hevo.Core.Interfaces.ITimeRangeBuilder","Hevo.Core.Interfaces.Model.IBaseTimeRange",
                "Hevo.Core.TrendReplyData","Hevo.Core.TrendReplyDataItem","Hevo.Core.FieldValue",
                "Hevo.Api.Quotes.Protocols.Factory.ParamFactory"};
            var types=names.Select(Find).SelectMany(t=>new[]{t}.Concat(t.GetInterfaces())).Distinct().Select(Describe).ToArray();
            var fields=Find("Hevo.DataModel.Business.QuoteFields").GetProperties(BindingFlags.Public|BindingFlags.Static)
                .Where(p=>System.Text.RegularExpressions.Regex.IsMatch(p.Name,"latest|average|avg|volume|amount|date|time|close"))
                .Select(p=>new{name=p.Name,type=p.PropertyType.ToString(),value=Convert.ToString(p.GetValue(null))}).ToArray();
            return new {status="managed_trend_preflight_only",query_executed=false,types,quote_fields=fields};
        }
        static Dictionary<string,object> Values(object source,Dictionary<object,string> fieldMap,ref int bytes)
        {
            var values=new Dictionary<string,object>();
            if(source==null)return values;
            foreach(DictionaryEntry pair in (IDictionary)source)
            {
                if(values.Count>=64)throw new InvalidOperationException("Trend field budget exceeded");
                object value=pair.Value;string sourceType=value?.GetType().FullName;
                object isBad=null,isNull=null,datatype=null;
                if(sourceType=="Hevo.Core.FieldValue")
                {
                    isBad=Get(value,"IsBad");isNull=Get(value,"IsNull");datatype=Convert.ToString(Get(value,"datatype"));value=Get(value,"value");
                }
                string text=value is DateTime dt?dt.ToString("O",CultureInfo.InvariantCulture):Convert.ToString(value,CultureInfo.InvariantCulture);
                if(text!=null&&text.Length>4096)throw new InvalidOperationException("Trend scalar budget exceeded");
                bytes+=Encoding.UTF8.GetByteCount(text??"")+256;
                if(bytes>2*1024*1024)throw new InvalidOperationException("Trend enumeration byte budget exceeded");
                string field=Convert.ToString(pair.Key,CultureInfo.InvariantCulture);
                values.Add(fieldMap.TryGetValue(pair.Key,out var name)?name:"field:"+field,
                    new {value=text,source_type=sourceType,value_type=value?.GetType().FullName,source_field=field,is_bad=isBad,is_null=isNull,datatype});
            }
            return values;
        }
        public static object Query(JsonElement request)
        {
            if(NativePending!=null&&!NativePending.IsCompleted)throw new InvalidOperationException("A native trend request is still pending; no retry allowed");
            Type dc=Find("Hevo.Core.DataCenter");
            if(dc.Module.ModuleVersionId!=Guid.Parse("43e5a11b-557e-4dbd-94cf-872ca400cbed"))throw new InvalidOperationException("Quotes module MVID changed");
            if(Find("Hevo.Core.Security").Module.ModuleVersionId!=Guid.Parse("b4ee3d18-b40b-404a-b483-0a3e35bdd19b"))throw new InvalidOperationException("DataModel module MVID changed");
            string code=request.GetProperty("code").GetString(),market=request.GetProperty("market").GetString(),date=request.GetProperty("date").GetString();
            var markets=new Dictionary<string,string>{{"17","USHA"},{"20","USHJ"},{"33","USZA"},{"36","USZJ"}};
            if(code==null||code.Length!=6||!code.All(c=>c>='0'&&c<='9')||(!markets.ContainsKey(market)&&!markets.ContainsValue(market)))throw new InvalidOperationException("Invalid security/market");
            DateTime tradeDate=DateTime.ParseExact(date,"yyyyMMdd",CultureInfo.InvariantCulture);
            object security=Activator.CreateInstance(Find("Hevo.Core.Security"),new object[]{market,code});
            string expectedMarket=markets.TryGetValue(market,out var alias)?alias:market;
            if((bool)Find("Hevo.Core.Security").GetMethod("IsNullOrBad",new[]{Find("Hevo.Core.Security")}).Invoke(null,new[]{security})||Convert.ToString(Get(security,"Code"))!=code||Convert.ToString(Get(security,"Market"))!=expectedMarket)throw new InvalidOperationException("Security identity mismatch");
            object current=Static("Hevo.Core.DataCenter","Current");
            if(current==null)throw new InvalidOperationException("No active DataCenter");
            object marketService=Static("Hevo.MarketData.Registry.MarketDataService","Instance");
            object marketTime=marketService.GetType().GetMethod("GetMarketTime",new[]{Find("Hevo.Core.Security")}).Invoke(marketService,new[]{security});
            object marketTimeInfo=marketTime==null?(object)new {available=false}:new {
                available=true,
                timezone_offset=Convert.ToInt64(Get(marketTime,"TimeZoneOffset")),
                trading_periods_raw=Convert.ToString(Get(marketTime,"TradingPeriodsRaw")),
                trading_periods=((IEnumerable)Get(marketTime,"TradingPeriods")).Cast<object>().Select(p=>new {
                    start=((DateTime)Get(p,"Start")).ToString("O",CultureInfo.InvariantCulture),
                    end=((DateTime)Get(p,"End")).ToString("O",CultureInfo.InvariantCulture)}).ToArray()};
            object accessor=Get(current,"DataAccessor");
            object factory=Find("Hevo.Core.Interfaces.Interface.DataAccess.IDataAccessor").GetProperty("ParameterFactory").GetValue(accessor);
            object builders=Find("Hevo.Core.Interfaces.IDataParameterFactory").GetProperty("Builders").GetValue(factory);
            object builder=Find("Hevo.Core.Interfaces.Interface.Factory.IBuildersFactory").GetMethod("CreateTimeRange").Invoke(builders,null);
            Type builderType=Find("Hevo.Core.Interfaces.ITimeRangeBuilder");
            builder=builderType.GetMethod("FromEarliest").Invoke(builder,null);
            builder=builderType.GetMethod("ToLatest").Invoke(builder,null);
            builder=builderType.GetMethod("OnTradeDate",new[]{typeof(DateTime)}).Invoke(builder,new object[]{tradeDate});
            object range=builderType.GetMethod("Build").Invoke(builder,null);
            var rangeInfo=Find("Hevo.Core.Interfaces.Model.IBaseTimeRange").GetProperties().ToDictionary(p=>p.Name,p=>Convert.ToString(p.GetValue(range),CultureInfo.InvariantCulture));
            if(rangeInfo["TradeDate"]!=date)throw new InvalidOperationException("Trade date builder mismatch");
            string[] names={"date_time","latest","transaction_volume","transaction_amount","prev_close","deal_average"};
            var fieldMap=new Dictionary<object,string>();Array fields=Array.CreateInstance(Find("Hevo.Core.QuoteField"),names.Length);
            for(int i=0;i<names.Length;i++){object f=Static("Hevo.DataModel.Business.QuoteFields",names[i]);fields.SetValue(f,i);fieldMap.Add(f,names[i]);}
            object trendFactory=Find("Hevo.Core.Interfaces.IDataParameterFactory").GetProperty("Trend").GetValue(factory);
            MethodInfo create=Find("Hevo.Core.Interfaces.ITrendParameterFactory").GetMethods().Single(m=>m.Name=="Create"&&m.GetParameters().Length==3);
            object parameters=create.Invoke(trendFactory,new[]{security,(object)fields,range});
            DateTimeOffset started=DateTimeOffset.UtcNow;
            NativePending=(Task)Find("Hevo.Core.Interfaces.Interface.DataAccess.IDataAccessor").GetMethod("RequestIntradayTrendDataAsync").Invoke(accessor,new[]{parameters});
            if(!NativePending.Wait(TimeSpan.FromSeconds(30)))return new {status="query_wait_timeout",request_may_still_complete=true,query_started_at=started,subscription_created=false};
            object reply=NativePending.GetType().GetProperty("Result").GetValue(NativePending);
            bool bad=(bool)Via(reply,"Hevo.Core.Interfaces.Dto.IReplyData","IsBad");
            object replySecurity=Via(reply,"Hevo.Core.Interfaces.Interface.Reponse.ITimeTableReplyData","Security");
            string replyCode=replySecurity==null?null:Convert.ToString(Get(replySecurity,"Code")),replyMarket=replySecurity==null?null:Convert.ToString(Get(replySecurity,"Market"));
            var rows=new List<object>();int bytes=0;
            if(!bad)
            {
                foreach(DictionaryEntry pair in (IDictionary)Via(reply,"Hevo.Core.Interfaces.Interface.Reponse.ITimeTableReplyData","TimeToDictionary"))
                {
                    if(rows.Count>=2000)throw new InvalidOperationException("Trend row budget exceeded; no truncated success");
                    DateTime time=(DateTime)pair.Key;
                    rows.Add(new {date_time=time.ToString("O",CultureInfo.InvariantCulture),date_time_kind=time.Kind.ToString(),fields=Values(pair.Value,fieldMap,ref bytes)});
                }
            }
            var extra=Values(Via(reply,"Hevo.Core.Interfaces.Interface.Reponse.ITimeTableReplyData","DataEx"),fieldMap,ref bytes);
            return new {status=bad?"error_response":replyCode!=code||replyMarket!=expectedMarket?"identity_mismatch":rows.Count==0?"empty_return_unverified":"trend_rows_received",
                code,requested_market=market,market=expectedMarket,requested_date=date,reply_code=replyCode,reply_market=replyMarket,reply_type=reply.GetType().FullName,is_bad=bad,
                request_range=rangeInfo,requested_fields=names,row_count=rows.Count,rows,data_ex=extra,
                market_time=marketTimeInfo,
                query_started_at=started,captured_at=DateTimeOffset.UtcNow,source_timestamp=(string)null,subscription_created=false,
                source="software RequestIntradayTrendDataAsync / ITrendReplyData.TimeToDictionary",source_kind="software_native"};
        }
    }
}

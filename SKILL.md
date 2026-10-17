---
name: ths-reader
description: "按用户请求，从本机已登录且通过版本校验的同花顺远航版读取真实资金持仓、当日委托成交，或沪深股票与场内ETF的日周月K线、本地MA/MACD与指定单日分时快照。只读采集；不下单、不自动写投资台账，不支持场外基金净值或持续订阅。"
---

# 同花顺只读读取（便携版）

本 Skill 自带读取代码、已验证托管桥及 Frida 离线安装包，不依赖原作者的项目目录。首次使用按 [安装与兼容说明](references/setup.md) 执行 `install.ps1`。用户自行安装并登录同花顺；不携带账户或登录信息。

按当次读取请求执行：

1. 用 `Get-Process happ,xiadan | Select-Object Id,ProcessName,Path` 核对当前进程和账户。账户对应 `xiadan.exe`，行情对应 `happ.exe`。不使用固定历史 PID，多个目标需明确选择；不自动登录或常驻采集。
2. 将下面的 `$taskSkill` 设置为当前实际 Skill 目录，调用相对路径入口。首次或报错时先运行 `scripts/doctor.py` 核对本机配置；具体命令见安装说明。
3. 读取 stdout 返回的 `result` JSON 路径。原件保存在当前 Skill 的 `private/` 独立任务目录，不自动修改调用项目的工作簿、状态或交易记录。需要归档时遵守调用项目规则并保留源路径与哈希。

```powershell
# $taskSkill 为当前实际安装目录；进程 ID 由本轮进程核对取得。
& "$taskSkill/scripts/invoke_reader.ps1" -Mode account -ProcessId $taskAccountPid
& "$taskSkill/scripts/invoke_reader.ps1" -Mode quote -ProcessId $taskQuotePid -SecurityCode 588220 -Market 20 -Periods week,month
```

市场分类：`17/USHA` 沪股、`20/USHJ` 沪基、`33/USZA` 深股、`36/USZJ` 深基。不能把股票和基金市场标识混用。已实测样本：688981→17、300750→33、588220/513010→20、159915→36。其他代码是否可用以真实返回为准，不假定所有基金都有交易行情。

周期为 `day/week/month`，可选一个或多个，默认三个周期。MA(5/10/20/30/60)、MACD(12/26/9) 按各周期完整序列本地计算，必须标为 `local_calculated`，不能称软件原生指标。MACD 前 33 根预热，历史不足保留 null。复权固定 `Exclude`；请求条数不保证限制实际返回长度。

区分采集状态与对账状态：`collected` 只是读取完成，`partial_unreconciled` 不可变成完整对账通过。资金余额、可用、可取、冻结、市值和总资产分别保留；不自动划分资金用途，不重复加计标准券/回购本金。

报告数据末日、采集时间、源时间未知及未完成周期。刷新、上游完整性和跨品种量额单位仍未全部验证。版本或 MVID 不匹配、错误、超时则停止，不去掉保护、不盲试接口、不把错误当零资产/零价格。超时不意味着软件底层请求已取消。

分时独立调用 `invoke_reader.ps1 -Mode trend -ProcessId CURRENT_HAPP_PID -SecurityCode 588220 -Market 20 -TradeDate 2026-10-16`，只读取显式单日快照，不订阅。当前本机验收范围为 588220/USHJ、2026-10-16 的 241 点；其他证券/日期、盘中新鲜度和跨机运行未验证。返回北京时间、价格、累计源量额；缺失均价为 null，不补造。稳定 quote 尚不支持分钟周期。

仅调用只读接口；不执行买卖、撤单、转账或认证。不支持账户历史查询、连续订阅、多日分时、场外基金净值、KDJ/RSI。此版本使用 Python、Frida 和 .NET；不是纯 Rust 或被动内存读取。shell 无法访问进程时，使用平台正常权限机制，不用 Computer Use/OCR 替代。

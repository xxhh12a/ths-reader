# 安装、兼容与调用

## 朋友需要准备

1. Windows x64，Python 3.11 或更高版本的 64 位 CPython。可使用本机 Codex bundled Python；安装脚本会优先检查它，也支持 `-PythonPath` 明确指定。包内不包含 Python。
2. 已安装同花顺远航版、打开软件并登录自己的目标账户。包内不包含同花顺客户端、账户、Cookie、密码或 Token。
3. 客户端必须通过 `supported-profile.json` 的精确文件哈希校验。这是已验证安装样本的兼容包，不能保证所有远航版版本或券商账户可用。其他版本需要重新验证原生 ABI 和返回，不应删除保护继续调用。

Frida 17.5.1 的 Windows x64 wheel 已随包提供，首次依赖安装无需联网；Python、同花顺和正常登录由使用者准备。无需安装 .NET SDK，直接使用包内桥接 DLL 和客户端已有 .NET 运行时。

## 安装

解压后，在包含 `install.ps1` 的 `ths-reader` 目录打开 PowerShell：

```powershell
.\install.ps1 -ThsRoot 'C:\同花顺远航版'
```

安装位置默认是当前用户的 `~/.agents/skills/ths-reader`，不会覆盖已存在的另一份技能。若 Python 未自动找到，指定本机已有解释器：

```powershell
.\install.ps1 -ThsRoot 'D:\同花顺远航版' -PythonPath 'D:\Python312\python.exe'
```

可只检查环境和版本，不安装或调用账户接口：

```powershell
.\install.ps1 -ThsRoot 'D:\同花顺远航版' -CheckOnly
```

若本机执行策略阻止 `.ps1`，可只为本次安装启动一个进程：`powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -ThsRoot 'C:\同花顺远航版'`。这不会永久更改系统执行策略。

安装脚本校验包内文件和客户端版本，再复制 Skill、将依赖装到 Skill 内的 `native-work/python-deps`，生成 `reader-config.json`。配置只存本机客户端根目录和 Python 路径，不存账户凭据。安装完成后如技能列表未刷新，开启新聊天；仍未出现再重启 Codex。

## 使用

向 Codex 说：“使用 ths-reader 读取当前资金和持仓”或“读取 588220 的月线和 MACD”。Codex应核对本轮进程，不需要用户提供历史 PID。

直接脚本调用也可：

```powershell
$taskSkill = Join-Path $env:USERPROFILE '.agents\skills\ths-reader'
Get-Process happ,xiadan | Select-Object Id,ProcessName,Path
# 将变量填为上面核对到的本轮进程 ID。
& "$taskSkill\scripts\invoke_reader.ps1" -Mode account -ProcessId $taskAccountPid
& "$taskSkill\scripts\invoke_reader.ps1" -Mode quote -ProcessId $taskQuotePid -SecurityCode 588220 -Market 20 -Periods month
```

路径可含空格和中文；读取输出在当前 Skill 的 `private/`，不在原作者电脑或原项目目录。`reader-config.example.json` 仅作配置字段示例，正常安装会生成实际配置。

## 结果与已知限制

stdout 返回简短 JSON：状态、结果路径、行数/日期或错误。完整结果 `result.json` 包含 `schema_version=1`、`operation`、`status`、采集时间、`source_timestamp`、`sources` 原件路径/哈希及 `limits`。账户附加 `funds/positions/valuation_status/reconciliation`；行情附加 `code/market/periods`，每周期含 bars、完整本地指标和 latest。

单日分时使用 `invoke_reader.ps1 -Mode trend -ProcessId CURRENT_HAPP_PID -SecurityCode 588220 -Market 20 -TradeDate 2026-10-16`。结果附加 `points/validation/native_metadata`，北京时间带 +08:00，量额为累计源单位。均价缺失为 null。只有上述本机单日样本完成语义对照，跨机和盘中实时未验收；原账户和日/周/月入口保持原行为。

`collected` 不代表完整对账、新鲜行情或上游无漏行。源时间通常为 null，最后一根可能尚未完结；量额聚合有小差异，跨品种单位未全部验证。其他代码、不同账户、其他系统和客户端版本需实测。分钟 K 线、多日分时与场外基金净值不支持。

错误结果保留原件、日志和未尝试周期；不自动重试、不覆盖原件、不写投资台账。查看任务目录的 worker 日志定位错误；客户端版本不匹配时停止并重新验证。Frida detach 后托管桥仍可能留在客户端直到退出。所有采集仅针对使用者已登录的会话。

## 再次分享

直接转发原始 ZIP。使用后的安装目录可能含你自己的 `private/`、日志和配置，不能直接整目录再压缩。干净分享包的逐文件 SHA-256 见 `manifest.json`；第三方依赖说明见 `THIRD_PARTY_NOTICES.md`。

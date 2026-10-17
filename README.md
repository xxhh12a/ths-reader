# ths-reader

Windows 上的同花顺远航版只读采集 Skill。通过本机已登录的客户端读取账户与行情，数据只保存到本机 `private/`，不执行交易或自动改写投资台账。

## 支持范围

| 入口 | 能力 | 边界 |
|---|---|---|
| `account` | 资金、持仓、当日委托、当日成交 | 非原子快照；刷新与上游完整性待验证 |
| `quote` | 沪深股票及场内 ETF 日/周/月 K 线；本地 MA、MACD | 固定 `Exclude`；不支持分钟 K 线、KDJ、RSI |
| `trend` | 显式指定单个交易日的分时快照 | 仅一个本机样本完成语义验收；跨机、其他日期及盘中新鲜度待验证 |

不支持场外基金净值、账户历史查询、持续订阅或自动登录。`collected` 代表采集完成，不能据此认定账户完整对账或行情实时刷新。MA/MACD 标为 `local_calculated`。

## 安装

准备 Windows x64、64 位 CPython 3.11+、通过精确版本校验的同花顺远航版。在下载并解压的仓库目录运行：

```powershell
.\install.ps1 -ThsRoot 'C:\同花顺远航版'
# 仅检查兼容性：
.\install.ps1 -ThsRoot 'C:\同花顺远航版' -CheckOnly
```

默认安装到当前用户 `~/.agents/skills/ths-reader`，不会覆盖已存在目录。包含 Frida 离线 wheel 和两个版本绑定的桥接 DLL；不包含 Python 或同花顺客户端。详细配置、调用与限制见 [安装说明](references/setup.md) 和 [SKILL.md](SKILL.md)。

## 仓库结构

- `SKILL.md`、`agents/`、`references/`：技能入口与使用契约。
- `native-probe/`：Python、Frida JavaScript 与当前 C# 读取源码。
- `native-work/quote-bridge/`：已验证的两个桥接 DLL，以及各自对应源码。
- `scripts/`：环境检查、调用入口、发布检查与离线回归验证。
- `vendor/`：原始 Frida wheel 与许可证；见 [第三方说明](THIRD_PARTY_NOTICES.md)。
- `manifest.json`：安装所需文件的大小与 SHA-256。

本仓库从明确文件清单构建，排除了账户样本、持仓、成交记录、资金金额、日志、个人路径、本机配置及原项目历史。使用后的安装目录含私有输出，分享时应使用干净仓库下载包。

## 本地检查

以下检查不连接同花顺、不查询真实账户：

```powershell
python -B scripts/test_offline.py
python -B scripts/check_release.py
```

发布检查针对 Git 暂存内容，并与 `manifest.json` 比对；GitHub ZIP 下载不含 `.git`，不能直接使用该检查。维护源码后，先暂存明确要发布的文件，再运行 `python -B scripts/update_manifest.py`，重新暂存清单并检查。

本次发布的审查范围、修复及验证见 [REVIEW.md](REVIEW.md)。

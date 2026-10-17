# 第三方依赖

本包 `vendor/wheels/` 带有 PyPI 发布的原始 Frida 17.5.1 Windows x64 wheel，未修改。Frida 采用 wxWindows Library Licence 3.1；许可证副本在 `vendor/FRIDA-COPYING`，wheel 自身也保留原始许可证和包元数据。项目来源：https://frida.re/ 。

同花顺客户端及其 .NET 运行时由使用者本机已有安装提供，本包不分发这些文件。两个 `ThsQuoteBridge.dll` 是本包读取器的辅助模块，各自对应的源码位于同级桥接目录；`native-probe/QuoteBridge.cs` 与 `TrendReader.cs` 是当前分时桥源码。包内未包含 Python 或 Codex 运行时。

param(
    [Parameter(Mandatory=$true)][ValidateSet('account','quote','trend')][string]$Mode,
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [string]$SecurityCode,
    [ValidateSet('17','20','33','36','USHA','USHJ','USZA','USZJ')][string]$Market,
    [ValidateSet('day','week','month')][string[]]$Periods=@('day','week','month'),
    [ValidateRange(1,2000)][int]$Count=200,
    [string]$TradeDate
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskConfigPath=Join-Path $taskRoot 'reader-config.json'
if(-not (Test-Path -LiteralPath $taskConfigPath)) {throw 'Run install.ps1 first'}
$taskConfig=Get-Content -LiteralPath $taskConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskPython=$taskConfig.python_executable
if(-not (Test-Path -LiteralPath $taskPython)) {throw 'Configured Python was not found'}
$env:PYTHONUTF8='1'
$env:THS_READER_CONFIG=$taskConfigPath
$taskArgs=@((Join-Path $taskRoot 'native-probe\read_ths.py'),$Mode.ToLowerInvariant(),'--pid',"$ProcessId")
if($Mode -eq 'quote') {
    if(-not $SecurityCode -or -not $Market) {throw 'quote requires SecurityCode and Market'}
    $taskArgs+=@('--code',$SecurityCode,'--market',$Market,'--count',"$Count",'--periods')
    $taskArgs+=$Periods
}
if($Mode -eq 'trend') {
    if(-not $SecurityCode -or -not $Market -or -not $TradeDate) {throw 'trend requires SecurityCode, Market and TradeDate'}
    $taskArgs+=@('--code',$SecurityCode,'--market',$Market,'--date',$TradeDate)
}
& $taskPython @taskArgs
exit $LASTEXITCODE

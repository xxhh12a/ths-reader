param(
    [Parameter(Mandatory=$true)][string]$ThsRoot,
    [string]$PythonPath,
    [string]$Destination=(Join-Path $env:USERPROFILE '.agents\skills\ths-reader'),
    [switch]$CheckOnly
)
$ErrorActionPreference='Stop'
$env:PYTHONUTF8='1'
$taskSource=$PSScriptRoot
$taskManifest=Get-Content -LiteralPath (Join-Path $taskSource 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach($taskFile in $taskManifest.files) {
    if([IO.Path]::IsPathRooted($taskFile.path) -or $taskFile.path -match '(^|[\\/])\.\.([\\/]|$)') {throw 'Invalid manifest path'}
    $taskPath=Join-Path $taskSource $taskFile.path
    if((Get-FileHash -Algorithm SHA256 -LiteralPath $taskPath).Hash -ne $taskFile.sha256) {throw "Package hash mismatch: $($taskFile.path)"}
}
if(-not $PythonPath) {
    $taskBundled=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if(Test-Path -LiteralPath $taskBundled) {$PythonPath=$taskBundled}
    else {
        $taskCommand=Get-Command python -ErrorAction SilentlyContinue
        if($taskCommand -and $taskCommand.Source -notmatch 'WindowsApps') {$PythonPath=$taskCommand.Source}
    }
}
if(-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) {throw 'Specify an existing 64-bit Python 3.11+ with -PythonPath'}
$taskPython=(Resolve-Path -LiteralPath $PythonPath).Path
$taskThsRoot=(Resolve-Path -LiteralPath $ThsRoot).Path
& $taskPython (Join-Path $taskSource 'scripts\doctor.py') --ths-root $taskThsRoot --skip-dependency
if($LASTEXITCODE -ne 0) {throw 'Client/Python compatibility check failed; installation stopped'}
if($CheckOnly) {exit 0}
$taskTarget=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination)
if(Test-Path -LiteralPath $taskTarget) {throw 'Destination already exists; keep it or choose a new -Destination. Existing skills are not overwritten.'}
New-Item -ItemType Directory -Path $taskTarget | Out-Null
foreach($taskFile in $taskManifest.files) {
    $taskOutput=Join-Path $taskTarget $taskFile.path
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $taskOutput) | Out-Null
    Copy-Item -LiteralPath (Join-Path $taskSource $taskFile.path) -Destination $taskOutput
}
Copy-Item -LiteralPath (Join-Path $taskSource 'manifest.json') -Destination (Join-Path $taskTarget 'manifest.json')
$taskConfig=[ordered]@{schema_version=1;ths_root=$taskThsRoot;python_executable=$taskPython}
$taskConfig | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskTarget 'reader-config.json') -Encoding UTF8
& $taskPython -m pip install --disable-pip-version-check --no-index --no-deps --find-links (Join-Path $taskTarget 'vendor\wheels') --target (Join-Path $taskTarget 'native-work\python-deps') -r (Join-Path $taskTarget 'requirements.txt')
if($LASTEXITCODE -ne 0) {throw 'Offline Frida installation failed; inspect the retained destination'}
& $taskPython (Join-Path $taskTarget 'scripts\doctor.py')
if($LASTEXITCODE -ne 0) {throw 'Installed reader compatibility check failed'}
[pscustomobject]@{status='installed';skill=$taskTarget;reader=(Join-Path $taskTarget 'scripts\invoke_reader.ps1');account_query_executed=$false} | ConvertTo-Json -Compress

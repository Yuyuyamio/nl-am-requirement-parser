param(
    [string]$ProjectRoot = 'E:\nl-am-requirement-parser-M2-source-20260804_152224',
    [string]$RequestId = 'M2-1E4B2301FADD'
)

$ErrorActionPreference = 'Stop'
$Task = Join-Path $ProjectRoot "outputs\m4\$RequestId"
$Monitor = Join-Path $ProjectRoot 'src\am_print_executor\gate8fb_x1c_native_ai_live_monitor_v860.py'

if (-not (Test-Path $Monitor)) { throw "Gate 8F-B monitor missing: $Monitor" }
if (-not (Test-Path $Task)) { throw "M4 task directory missing: $Task" }

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Archive = Join-Path $Task "archive\gate8fb_v860_$Stamp"
New-Item -ItemType Directory -Force $Archive | Out-Null

$Old = Get-ChildItem $Task -File | Where-Object {
    $_.Name -match 'v860' -and $_.Name -match 'gate8fb|native_ai_live'
}

if ($Old) {
    $Old | Move-Item -Destination $Archive
    Write-Host "Archived old Gate 8F-B evidence to: $Archive" -ForegroundColor Yellow
} else {
    Write-Host "No existing Gate 8F-B evidence needed archiving." -ForegroundColor DarkGray
}

$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
python $Monitor
exit $LASTEXITCODE

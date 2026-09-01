param(
    [string]$ProjectRoot = "",
    [int]$WaitForStartSeconds = 600,
    [int]$MaxMonitorSeconds = 21600
)
$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = Join-Path "E:\" "nl-am-requirement-parser-M2-source-20260804_152224"
}
Set-Location -LiteralPath $ProjectRoot
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = (Resolve-Path -LiteralPath $SourceRoot).Path
python -m am_print_executor.gate5_runtime_v500 monitor `
    --project-root $ProjectRoot `
    --wait-for-start-seconds $WaitForStartSeconds `
    --max-monitor-seconds $MaxMonitorSeconds
exit $LASTEXITCODE

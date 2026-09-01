param([string]$ProjectRoot = "")
$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $DriveRoot = "E:\"
    $ProjectRoot = Join-Path $DriveRoot "nl-am-requirement-parser-M2-source-20260804_152224"
}
Set-Location -LiteralPath $ProjectRoot
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = (Resolve-Path -LiteralPath $SourceRoot).Path
python -m am_print_executor.phase4_runtime_cli_v401 preflight --project-root $ProjectRoot
exit $LASTEXITCODE

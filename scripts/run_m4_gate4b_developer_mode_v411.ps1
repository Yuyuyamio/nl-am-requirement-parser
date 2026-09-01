param([string]$ProjectRoot = "")
$ErrorActionPreference = "Stop"
if (-not $ProjectRoot) {
    $ProjectRoot = Join-Path "E:\" "nl-am-requirement-parser-M2-source-20260804_152224"
}
Set-Location -LiteralPath $ProjectRoot
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = (Resolve-Path -LiteralPath $SourceRoot).Path
python -m am_print_executor.phase4b_developer_mode_cli_v411 --project-root $ProjectRoot
exit $LASTEXITCODE

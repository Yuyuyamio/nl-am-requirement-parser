param([string]$ProjectRoot = "")
$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = Join-Path "E:\" "nl-am-requirement-parser-M2-source-20260804_152224"
}

Set-Location -LiteralPath $ProjectRoot
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = (Resolve-Path -LiteralPath $SourceRoot).Path

python -m am_print_executor.gate7b_profile_patch_contract_cli_v710 --project-root $ProjectRoot
exit $LASTEXITCODE

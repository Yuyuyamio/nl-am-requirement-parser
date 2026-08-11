param(
    [string]$ProjectRoot = "",
    [string]$RequestId = "M2-1E4B2301FADD",
    [ValidateRange(1, 10)]
    [int]$Attempts = 1
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $DriveRoot = "E:\"
    $ProjectRoot = Join-Path $DriveRoot "nl-am-requirement-parser-M2-source-20260804_152224"
}

Set-Location -LiteralPath $ProjectRoot
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = (Resolve-Path -LiteralPath $SourceRoot).Path

python -m am_print_executor.phase1_autodiscovery_cli `
    --project-root $ProjectRoot `
    --request-id $RequestId `
    --attempts $Attempts

exit $LASTEXITCODE

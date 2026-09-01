param(
    [string]$ProjectRoot = "",
    [string]$RequestId = "M2-1E4B2301FADD",
    [ValidateRange(1, 3)]
    [int]$Attempts = 1,
    [string]$RemoteDir = "/cache"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $DriveRoot = "E:\"
    $ProjectRoot = Join-Path $DriveRoot "nl-am-requirement-parser-M2-source-20260804_152224"
}

$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = (Resolve-Path -LiteralPath $SourceRoot).Path

python -m am_print_executor.phase2_ftps_cli_v32 `
    --project-root $ProjectRoot `
    --request-id $RequestId `
    --attempts $Attempts `
    --remote-dir $RemoteDir

exit $LASTEXITCODE

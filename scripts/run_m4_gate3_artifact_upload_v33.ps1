param(
    [string]$ProjectRoot = "",
    [string]$RequestId = "M2-1E4B2301FADD",
    [string]$RemoteDir = "/cache"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $DriveRoot = "E:\"
    $ProjectRoot = Join-Path $DriveRoot "nl-am-requirement-parser-M2-source-20260804_152224"
}

$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = (Resolve-Path -LiteralPath $SourceRoot).Path

python -m am_print_executor.phase3_artifact_upload_cli_v33 `
    --project-root $ProjectRoot `
    --request-id $RequestId `
    --remote-dir $RemoteDir

exit $LASTEXITCODE

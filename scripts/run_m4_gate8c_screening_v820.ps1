param(
    [Parameter(Mandatory=$true)]
    [string]$SuccessDir,

    [Parameter(Mandatory=$true)]
    [string]$FailureDir,

    [string]$SequenceManifest = "",
    [string]$ProjectRoot = "",
    [string]$PrintGuardBaseUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = Join-Path "E:\" "nl-am-requirement-parser-M2-source-20260804_152224"
}

Set-Location -LiteralPath $ProjectRoot
$SourceRoot = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = (Resolve-Path -LiteralPath $SourceRoot).Path

$ResolvedSuccess = (Resolve-Path -LiteralPath $SuccessDir).Path
$ResolvedFailure = (Resolve-Path -LiteralPath $FailureDir).Path

$ArgsList = @(
    "-m",
    "am_print_executor.gate8c_model_screening_cli_v820",
    "--project-root",
    $ProjectRoot,
    "--success-dir",
    $ResolvedSuccess,
    "--failure-dir",
    $ResolvedFailure,
    "--base-url",
    $PrintGuardBaseUrl
)

if ($SequenceManifest) {
    $ResolvedManifest = (Resolve-Path -LiteralPath $SequenceManifest).Path
    $ArgsList += "--sequence-manifest"
    $ArgsList += $ResolvedManifest
}

& python @ArgsList
exit $LASTEXITCODE

param(
    [Parameter(Mandatory=$true)]
    [string[]]$ImagePath,
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

$ArgsList = @(
    "-m",
    "am_print_executor.gate8b_printguard_adapter_cli_v810",
    "--project-root",
    $ProjectRoot,
    "--base-url",
    $PrintGuardBaseUrl
)

foreach ($PathItem in $ImagePath) {
    $ResolvedImage = (Resolve-Path -LiteralPath $PathItem).Path
    $ArgsList += "--image"
    $ArgsList += $ResolvedImage
}

& python @ArgsList
exit $LASTEXITCODE

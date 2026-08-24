[CmdletBinding()]
param(
    [Parameter()]
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    [Parameter()]
    [string]$OutputRoot = "outputs/automatic_jobs",

    [Parameter()]
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Project Python is unavailable. Recreate .venv before starting the UI."
}

$serverArguments = @(
    "-m",
    "am_print_frontend.server",
    "--host",
    "127.0.0.1",
    "--port",
    "$Port",
    "--output-root",
    "$OutputRoot"
)
if ($NoBrowser) {
    $serverArguments += "--no-browser"
}

Push-Location $projectRoot
try {
    & $pythonExecutable @serverArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

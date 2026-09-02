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
$sourceRoot = Join-Path $projectRoot "src"
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
$previousPythonPath = $env:PYTHONPATH

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Project Python is unavailable. Recreate .venv before starting the UI."
}

$serverArguments = @(
    "-m",
    "am_print_frontend",
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
    # Always load the current checkout. This prevents an older installed copy
    # from silently restoring retired post-slice validation logic.
    $env:PYTHONPATH = if ($previousPythonPath) {
        $sourceRoot + [IO.Path]::PathSeparator + $previousPythonPath
    }
    else {
        $sourceRoot
    }
    & $pythonExecutable @serverArguments
    exit $LASTEXITCODE
}
finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPythonPath
    }
    Pop-Location
}

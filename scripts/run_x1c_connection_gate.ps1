[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$PrinterIP = "172.16.61.6",

    [Parameter()]
    [string]$DeviceID = "00M09A3A1700722",

    [Parameter()]
    [ValidateRange(1, 120)]
    [double]$TimeoutSeconds = 20,

    [Parameter()]
    [switch]$KeepOpen
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $projectRoot "src"
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Project Python is unavailable. Recreate .venv before running the gate."
}

$previousPythonPath = $env:PYTHONPATH
$connectionExitCode = 1
try {
    $env:PYTHONPATH = $sourceRoot
    & $pythonExecutable `
        -m am_print_executor.x1c_connection_cli `
        --ip $PrinterIP `
        --device-id $DeviceID `
        --timeout $TimeoutSeconds
    $connectionExitCode = $LASTEXITCODE
}
finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPythonPath
    }
}

if ($KeepOpen) {
    [void](Read-Host "Press Enter to close this window")
}
exit $connectionExitCode

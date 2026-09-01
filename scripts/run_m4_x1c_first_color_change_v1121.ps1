#requires -Version 5.1

param(
    [Parameter(Mandatory=$true)]
    [string]$JobRequestId,

    [Parameter(Mandatory=$true)]
    [string]$Artifact,

    [Parameter(Mandatory=$true)]
    [string]$AmsMapping,

    [string]$ProjectRoot = '',

    [string]$DeviceEvidenceRequestId = 'M2-1E4B2301FADD',

    [int]$MonitorSeconds = 7200,

    [switch]$ConfirmStart
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}

$Root = (
    Resolve-Path -LiteralPath $ProjectRoot
).Path

$Py = Join-Path `
    $Root `
    '.venv\Scripts\python.exe'

$Wrapper = Join-Path `
    $Root `
    'src\am_print_executor\developer_mode_final_acceptance_rootfix_v1120.py'

$Artifact = (
    Resolve-Path -LiteralPath $Artifact
).Path

foreach ($req in @($Py, $Wrapper, $Artifact)) {
    if (-not (Test-Path -LiteralPath $req -PathType Leaf)) {
        throw "Required file missing: $req"
    }
}

$env:NL_AM_PROJECT_ROOT = $Root
$env:PYTHONPATH = Join-Path $Root 'src'

Set-Location -LiteralPath $Root

Write-Host '=== M4 X1C GENERIC COLOR-CHANGE ACCEPTANCE V11.2.1 ==='
Write-Host "Project root              : $Root"
Write-Host "Job request ID            : $JobRequestId"
Write-Host "Device evidence request ID: $DeviceEvidenceRequestId"
Write-Host "Artifact                  : $Artifact"
Write-Host "AMS mapping               : $AmsMapping"
Write-Host ""

$Args = @(
    $Wrapper,
    '--artifact',
    $Artifact,
    '--ams-mapping',
    $AmsMapping,
    '--monitor-seconds',
    [string]$MonitorSeconds,
    '--project-root',
    $Root,
    '--job-request-id',
    $JobRequestId,
    '--device-evidence-request-id',
    $DeviceEvidenceRequestId
)

if (-not $ConfirmStart) {
    Write-Host 'MODE=DRY_RUN'
    Write-Host 'No Access Code will be requested.'
    Write-Host 'No FTPS/MQTT connection will be made.'
    Write-Host 'No print command will be sent.'
    Write-Host ''

    $Args += '--preflight-only'
}

if ($ConfirmStart) {
    $studio = Get-Process `
        -Name 'bambu-studio' `
        -ErrorAction SilentlyContinue

    if ($studio) {
        throw 'Bambu Studio is running. Close it before live acceptance.'
    }

    Write-Host 'MODE=REAL_PRINT'
    Write-Host 'This run may upload and start a physical print.'
    Write-Host ''
}

& $Py @Args

$rc = $LASTEXITCODE

Write-Host ''
Write-Host "M4_GENERIC_ACCEPTANCE_EXIT=$rc"

exit $rc

#requires -Version 5.1
$ErrorActionPreference = "Stop"

$Root = 'E:\nl-am-requirement-parser-M2-source-20260804_152224'
$TaskDir = Join-Path $Root 'outputs\m4\M2-1E4B2301FADD'
$Wrapper = Join-Path $Root 'src\am_print_executor\developer_mode_final_acceptance_rootfix_v1120.py'
$Artifact = Join-Path $TaskDir 'final_assets_v1070\originium_slug_x1c_bicolor_clean_v1070.gcode.3mf'

$MonitorSeconds = 7200
$Mapping = '0,3'

Write-Host "=== M4 X1C FIRST COLOR-CHANGE LIVE ACCEPTANCE V11.2.1 ===" -ForegroundColor Cyan
Write-Host "REAL PRINT." -ForegroundColor Yellow
Write-Host ""
Write-Host "Purpose:"
Write-Host "  Validate the corrected X1C raw AMS wire mapping on the first real color change."
Write-Host ""
Write-Host "Locked wire rule:"
Write-Host "  CLI mapping        : [0,3]"
Write-Host "  X1C wire mapping   : [0,3,-1,-1,-1]"
Write-Host "  ams_mapping2       : OMIT"
Write-Host "  ams_mapping_info   : OMIT"
Write-Host ""
Write-Host "Physical AMS:"
Write-Host "  Slot 1 = GRAY PLA"
Write-Host "  Slot 4 = YELLOW PLA"
Write-Host ""

foreach ($req in @($Wrapper, $Artifact)) {
    if (-not (Test-Path -LiteralPath $req -PathType Leaf)) {
        throw "Required file missing: $req"
    }
}

$studio = Get-Process -Name 'bambu-studio' -ErrorAction SilentlyContinue
if ($studio) {
    throw "Bambu Studio is running. Close it completely before this live acceptance."
}

# Archive only V11.2 evidence from an earlier attempt, if any.
$evidence = @(
    (Join-Path $TaskDir 'm4_developer_backend_final_live_events_v1120.jsonl'),
    (Join-Path $TaskDir 'm4_developer_backend_final_acceptance_v1120.json')
) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }

if ($evidence.Count -gt 0) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $archive = Join-Path $TaskDir "archive\before_v1121_live_$stamp"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    foreach ($e in $evidence) {
        Move-Item -LiteralPath $e -Destination $archive -Force
    }
    Write-Host "[ARCHIVE] Previous V11.2 evidence -> $archive"
}

Write-Host ""
Write-Host "=== IMPORTANT ===" -ForegroundColor Yellow
Write-Host "No standalone TCP probe."
Write-Host "No Test-NetConnection."
Write-Host "No AMS remapping step."
Write-Host "No Bambu Studio GUI."
Write-Host ""
Write-Host "The first decisive checkpoint is the FIRST GRAY -> YELLOW change."
Write-Host "If the printer reports 'Failed to get AMS mapping table' again,"
Write-Host "DO NOT rerun. Stop/cancel the print normally and send the exact printer error + terminal tail."
Write-Host ""

$env:PYTHONPATH = (Join-Path $Root 'src') + ';' + $env:PYTHONPATH
Set-Location $Root

& python $Wrapper `
    --artifact $Artifact `
    --ams-mapping $Mapping `
    --monitor-seconds $MonitorSeconds

$rc = $LASTEXITCODE

Write-Host ""
if ($rc -eq 0) {
    Write-Host "M4_X1C_FIRST_COLOR_CHANGE_V1121=PASS" -ForegroundColor Green
    Write-Host ""
    Write-Host "This means the V11.2 print lifecycle completed successfully."
    Write-Host "Report:"
    Write-Host "  $(Join-Path $TaskDir 'm4_developer_backend_final_acceptance_v1120.json')"
    Write-Host "Events:"
    Write-Host "  $(Join-Path $TaskDir 'm4_developer_backend_final_live_events_v1120.jsonl')"
} else {
    Write-Host "M4_X1C_FIRST_COLOR_CHANGE_V1121=NOT_PASS" -ForegroundColor Red
    Write-Host "Python exit code: $rc"
    Write-Host ""
    Write-Host "Do not automatically rerun."
    Write-Host "If the print started, check the printer screen first."
    exit $rc
}

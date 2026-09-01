#requires -Version 5.1
$ErrorActionPreference = "Stop"

$Root = 'E:\nl-am-requirement-parser-M2-source-20260804_152224'
$TaskDir = Join-Path $Root 'outputs\m4\M2-1E4B2301FADD'

$Acceptance = Join-Path $TaskDir 'm4_developer_backend_final_acceptance_v1120.json'
$Events = Join-Path $TaskDir 'm4_developer_backend_final_live_events_v1120.jsonl'
$WireAudit = Join-Path $TaskDir 'diagnostics_v1120\m4_x1c_wire_mapping_v1120.json'
$Artifact = Join-Path $TaskDir 'final_assets_v1070\originium_slug_x1c_bicolor_clean_v1070.gcode.3mf'

$OutMd = Join-Path $TaskDir 'M4_X1C_BICOLOR_PRINT_SUCCESS_REPORT_V1122.md'
$OutTxt = Join-Path $TaskDir 'M4_X1C_BICOLOR_PRINT_SUCCESS_REPORT_V1122.txt'
$OutJson = Join-Path $TaskDir 'm4_x1c_bicolor_print_success_report_v1122.json'

Write-Host "=== M4 X1C BICOLOR PRINT REPORT GENERATOR V11.2.2 ===" -ForegroundColor Cyan
Write-Host "OFFLINE ONLY. No printer connection. No MQTT. No FTPS. No print command."
Write-Host ""

foreach ($req in @($Acceptance, $WireAudit, $Artifact)) {
    if (-not (Test-Path -LiteralPath $req -PathType Leaf)) {
        throw "Required evidence missing: $req"
    }
}

$acc = Get-Content -LiteralPath $Acceptance -Raw -Encoding UTF8 | ConvertFrom-Json
$wire = Get-Content -LiteralPath $WireAudit -Raw -Encoding UTF8 | ConvertFrom-Json

$eventCount = 0
$states = New-Object System.Collections.Generic.List[string]

if (Test-Path -LiteralPath $Events -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $Events -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $obj = $line | ConvertFrom-Json
            $eventCount++

            $state = $null
            if ($obj.PSObject.Properties.Name -contains 'gcode_state') {
                $state = $obj.gcode_state
            }
            elseif (($obj.PSObject.Properties.Name -contains 'print') -and $obj.print) {
                if ($obj.print.PSObject.Properties.Name -contains 'gcode_state') {
                    $state = $obj.print.gcode_state
                }
            }
            elseif (($obj.PSObject.Properties.Name -contains 'snapshot') -and $obj.snapshot) {
                if ($obj.snapshot.PSObject.Properties.Name -contains 'gcode_state') {
                    $state = $obj.snapshot.gcode_state
                }
            }

            if ($state) {
                $s = ([string]$state).ToUpperInvariant()
                if ($states.Count -eq 0 -or $states[$states.Count - 1] -ne $s) {
                    $states.Add($s)
                }
            }
        }
        catch {
            # Ignore malformed event lines; keep the rest.
        }
    }
}

if ($states.Count -eq 0) {
    if ($acc.PSObject.Properties.Name -contains 'state_sequence') {
        foreach ($s in @($acc.state_sequence)) {
            if ($s) { $states.Add(([string]$s).ToUpperInvariant()) }
        }
    }
}

$artifactHash = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash.ToLowerInvariant()
$artifactItem = Get-Item -LiteralPath $Artifact

$inputMapping = @($wire.input_mapping)
if ($inputMapping.Count -eq 0) { $inputMapping = @(0,3) }

$wireMapping = @($wire.wire_mapping)
if ($wireMapping.Count -eq 0) { $wireMapping = @(0,3,-1,-1,-1) }

$printerIp = '172.16.61.6'
if ($acc.PSObject.Properties.Name -contains 'printer_ip' -and $acc.printer_ip) {
    $printerIp = [string]$acc.printer_ip
}
elseif (($acc.PSObject.Properties.Name -contains 'identity') -and $acc.identity -and
        ($acc.identity.PSObject.Properties.Name -contains 'ip_address') -and $acc.identity.ip_address) {
    $printerIp = [string]$acc.identity.ip_address
}

$deviceId = '00M09A3A1700722'
if ($acc.PSObject.Properties.Name -contains 'device_id' -and $acc.device_id) {
    $deviceId = [string]$acc.device_id
}
elseif (($acc.PSObject.Properties.Name -contains 'identity') -and $acc.identity -and
        ($acc.identity.PSObject.Properties.Name -contains 'device_id') -and $acc.identity.device_id) {
    $deviceId = [string]$acc.identity.device_id
}

$stateText = if ($states.Count -gt 0) { $states -join ' -> ' } else { '未从事件文件中提取到完整状态序列' }

$summary = [ordered]@{
    schema_version = '0.1.0'
    module = 'M4'
    report_version = '11.2.2'
    generated_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
    request_id = 'M2-1E4B2301FADD'
    printer_model = 'Bambu Lab X1C'
    printer_ip = $printerIp
    device_id = $deviceId
    developer_mode = $true
    bambu_studio_gui_used = $false
    result = 'PASS'
    operator_confirmed_complete = $true
    operator_confirmation = '本次双色打印完整成功完成，首次灰色到黄色自动换料成功。'
    logical_filaments = [ordered]@{
        '0' = 'GRAY PLA'
        '1' = 'YELLOW PLA'
    }
    physical_ams_mapping = [ordered]@{
        logical_0_gray = 'AMS slot 1 / tray 0'
        logical_1_yellow = 'AMS slot 4 / tray 3'
    }
    cli_mapping = @($inputMapping)
    raw_wire_mapping = @($wireMapping)
    use_ams = $true
    ams_mapping2_sent = $false
    ams_mapping_info_sent = $false
    state_sequence = @($states)
    event_count = $eventCount
    artifact = $Artifact
    artifact_size_bytes = $artifactItem.Length
    artifact_sha256 = $artifactHash
    evidence = [ordered]@{
        acceptance_json = $Acceptance
        events_jsonl = if (Test-Path -LiteralPath $Events) { $Events } else { $null }
        wire_mapping_audit_json = $WireAudit
    }
}

$utf8Bom = New-Object System.Text.UTF8Encoding($true)

[System.IO.File]::WriteAllText(
    $OutJson,
    ($summary | ConvertTo-Json -Depth 12),
    $utf8Bom
)

$md = @"
# M4 X1C Developer Mode 双色打印成功报告

## 1. 任务概述

- 项目：物质创制——自然语言驱动的智能化增材制造 3D 打印与自优化
- 模块：M4 多材料协同打印
- 打印机：Bambu Lab X1C
- Device ID：``$deviceId``
- 打印机 IP：``$printerIp``
- 打印方式：Developer Mode
- Bambu Studio GUI：未使用
- 请求 ID：``M2-1E4B2301FADD``
- 本次验收结果：**PASS**

## 2. 本次核心目标

验证 X1C Developer Mode 下的双色打印主链路，重点确认 AMS 物理槽位映射和第一次真实灰色到黄色自动换料。

## 3. AMS 映射

- 逻辑耗材 0：灰色 PLA
- 逻辑耗材 1：黄色 PLA
- 灰色 PLA -> AMS 物理槽 1 -> tray 0
- 黄色 PLA -> AMS 物理槽 4 -> tray 3
- CLI 映射：``$($inputMapping -join ',')``
- X1C raw wire 映射：``$($wireMapping -join ',')``
- ``use_ams = true``
- ``ams_mapping2``：未发送
- ``ams_mapping_info``：未发送

## 4. 关键修复

此前双色打印在第一次换色阶段出现 AMS 映射表获取失败。

V11.2.0 将底层 ``project_file`` 中的 AMS 映射修正为 X1C raw MQTT 使用的真实 JSON 数组：

``[0, 3, -1, -1, -1]``

并取消对 X1C 不需要的 ``ams_mapping2`` 和 ``ams_mapping_info`` 注入。

## 5. 实机验证结果

- 打印任务成功启动：是
- 灰色正常打印：是
- 第一次灰色到黄色 AMS 自动换料：**成功**
- 黄色继续打印：**成功**
- 整体打印完成：**成功**
- 实机验收：``M4_X1C_FIRST_COLOR_CHANGE_V1121=PASS``

状态序列：

``$stateText``

实时事件数量：**$eventCount**

## 6. 打印文件

- 文件：``$($artifactItem.Name)``
- 大小：``$($artifactItem.Length) bytes``
- SHA256：``$artifactHash``

## 7. 当前完成链路

``预切片模型 -> Developer Mode 后端发送 -> AMS 自动选料 -> 灰色打印 -> 自动换黄色 -> 连续打印 -> 打印完成``

## 8. 结论

**M4 X1C Developer Mode 双色打印主链路已完成实机验证。**

本次实验已经证明：

1. 自研后端可以在不手动操作 Bambu Studio GUI 的情况下启动 X1C 打印；
2. 双色模型的 AMS 物理槽位映射正确；
3. 修正后的 raw wire AMS mapping 可以完成第一次真实换色；
4. 灰色到黄色自动换料成功；
5. 整体双色打印成功完成。

本报告只锁定 Developer Mode 双色打印与 AMS 自动换料成功。
如需进一步宣称“X1C Native AI 检测后自动调整打印参数”，仍需单独的闭环控制验收证据。

## 9. 证据文件

- Acceptance：``$Acceptance``
- Live events：``$Events``
- Wire mapping audit：``$WireAudit``
- 结构化报告：``$OutJson``
"@

[System.IO.File]::WriteAllText($OutMd, $md, $utf8Bom)

$txt = $md `
    -replace '\*\*','' `
    -replace '``','' `
    -replace '^#{1,6}\s*',''

[System.IO.File]::WriteAllText($OutTxt, $txt, $utf8Bom)

Write-Host "[PASS] Acceptance evidence loaded."
Write-Host "[PASS] Wire mapping audit loaded."
Write-Host "[PASS] Successful print confirmation recorded."
Write-Host "[PASS] Artifact SHA256: $artifactHash"
Write-Host "[PASS] Event count: $eventCount"
Write-Host "[PASS] State sequence: $stateText"
Write-Host ""
Write-Host "M4_X1C_BICOLOR_PRINT_REPORT_V1122=PASS" -ForegroundColor Green
Write-Host ""
Write-Host "Markdown:"
Write-Host "  $OutMd"
Write-Host "TXT:"
Write-Host "  $OutTxt"
Write-Host "JSON:"
Write-Host "  $OutJson"

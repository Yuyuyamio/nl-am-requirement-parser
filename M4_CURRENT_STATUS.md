# M4 Current Status

Updated: 2026-08-11

## Locked project state

- Module: M4
- Request ID: `M2-1E4B2301FADD`
- X1C device ID: `00M09A3A1700722`
- Branch: `agent/m4-phase4b-to-gate8-progress`

## Completed chain

- Gate 1 V3.1: PASS
- Gate 2 V3.2: PASS
- Gate 3 V3.3.1: PASS — do not re-upload the formal artifact
- Gate 4A V4.0.7: PASS
- Gate 4B V4.1.1 first real physical print: PASS
- Gate 5A V5.0.0 post-print acceptance: PASS
- Gate 5B: DEFERRED_TO_FINAL_LAB
- Gate 6A V6.0.0: PASS
- Gate 6B V6.1.0: PASS
- Gate 6C V6.2.0: PASS
- Gate 7 V7.0.0: PASS
- Gate 7B V7.1.0: PASS
- Gate 8 V8.0.0: PASS

## Gate 8C — PrintGuard screening

- success recall: 0.20
- failure recall: 0.7666666667
- balanced accuracy: 0.4833333333
- result: NOT ACCEPTED as the selected final visual model

Evidence:

`reports/m4/M2-1E4B2301FADD/m4_gate8c_model_screening_multiframe_v820.json`

## Gate 8D V8.3.1 — replacement candidate

Model: YOLO26n classification transfer learning.

Held-out test:

- sample count: 75
- multiclass accuracy: 0.5866666667
- binary accuracy: 0.7733333333
- success recall: 0.6666666667
- failure recall: 0.80
- balanced accuracy: 0.7333333333
- balanced-accuracy delta vs PrintGuard: +0.25

Status:

`CANDIDATE_METRICS_READY_FOR_REVIEW`

Evidence:

`reports/m4/M2-1E4B2301FADD/m4_gate8d_replacement_model_candidate_v831.json`

Model artifact:

`artifacts/m4/gate8d_yolo26_v831_best.pt`

Model SHA256:

`2e4ff3f65c4fe3431793f81511bddce06602ef2baebbf1e874775111e55801a5`

Important: the 73.33% balanced accuracy is a held-out result on the project dataset. It is NOT an X1C-camera-domain or production accuracy claim.

## Gate 8E V8.4.0 — deployment adapter

Status: PASS

- status: `deployment_adapter_validated`
- sample count: 75
- class match count: 75
- binary match count: 75
- max confidence absolute delta: 0.0
- passed: true
- weights SHA256: `2e4ff3f65c4fe3431793f81511bddce06602ef2baebbf1e874775111e55801a5`

Evidence:

`reports/m4/M2-1E4B2301FADD/m4_gate8e_deployment_adapter_validation_v840.json`

## Architecture correction before the next gate

Bambu X1C already has native AI monitoring capabilities.

Therefore the project must not treat the custom YOLO model as a replacement for X1C native AI by default.

Current direction:

1. Audit X1C native AI monitoring first.
2. Determine whether native AI intervention can be observed through LAN/MQTT/report/HMS/print-state signals.
3. Keep the custom YOLO model as a secondary/research detector.
4. Keep automatic pause/stop disabled until live validation.
5. Keep Gate 5B live MQTT monitoring, X1C camera validation, and live fusion for the final lab session.

## Next phase

`M4 Gate 8F-A: X1C Native AI Integration Audit`

Do not jump directly to the previous multiframe shadow-fusion plan.

Gate 8F-A should first determine:

- which X1C native AI functions are relevant;
- how those functions are enabled/configured;
- what observable state changes occur when native AI detects a failure;
- whether LAN/MQTT/report/HMS exposes a native-AI signal;
- if no explicit AI field exists, whether native AI intervention can be inferred safely;
- where the custom YOLO secondary detector belongs in the fusion architecture.

## Safety / reproducibility locks

- Do not re-run Gate 3 upload.
- Do not re-run the first real Gate 4B print.
- Do not run Gate 5B in the office.
- Do not expose or commit the X1C Access Code.
- Do not claim PrintGuard as accepted.
- Do not claim the Gate 8D model is validated on X1C live-camera data.
- Do not enable automatic pause/stop.
- Do not mutate G-code directly.
- Do not auto-apply Bambu profile patches.

## 2026-08-12 — X1C Developer Mode bicolor acceptance

Status: **PASS**

Final validated path:

`pre-sliced X1C .gcode.3mf -> Developer Mode backend -> AMS selection -> gray print -> automatic gray-to-yellow change -> continued print -> FINISH`

Key results:

- V11.2.0 rebuilt the raw X1C `project_file` AMS mapping at the backend layer.
- Logical filament mapping: gray -> AMS slot 1 / tray 0; yellow -> AMS slot 4 / tray 3.
- X1C wire mapping: `[0, 3, -1, -1, -1]`.
- `ams_mapping` is a real JSON array, not a nested JSON string.
- X1C path omits H2-style `ams_mapping2` and `ams_mapping_info`.
- Offline wire dry-run: `V1120_WIRE_DRYRUN=PASS`.
- Full real bicolor print: `M4_X1C_FIRST_COLOR_CHANGE_V1121=PASS`.
- First gray -> yellow AMS change completed successfully.
- The full print completed successfully without manual Bambu Studio GUI operation.

Evidence committed with this update:

- `reports/m4/M2-1E4B2301FADD/m4_x1c_wire_mapping_v1120.json`
- `reports/m4/M2-1E4B2301FADD/m4_developer_backend_final_acceptance_v1120.json`
- `reports/m4/M2-1E4B2301FADD/m4_developer_backend_final_live_events_v1120.jsonl`

Boundary: automatic runtime parameter adjustment triggered by X1C Native AI is still **not validated** and must not be claimed as complete.
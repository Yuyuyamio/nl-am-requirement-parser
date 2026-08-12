#requires -Version 5.1
$ErrorActionPreference = "Stop"

$Root = 'E:\nl-am-requirement-parser-M2-source-20260804_152224'
$SrcDir = Join-Path $Root 'src\am_print_executor'
$TaskDir = Join-Path $Root 'outputs\m4\M2-1E4B2301FADD'

$BackendBase = Join-Path $SrcDir 'developer_mode_backend_v1000.py'
$AcceptanceBase = Join-Path $SrcDir 'developer_mode_final_acceptance_v1062.py'
$WrapperBase = Join-Path $SrcDir 'developer_mode_final_acceptance_rootfix_v1070.py'

$BackendOut = Join-Path $SrcDir 'developer_mode_backend_v1120.py'
$AcceptanceOut = Join-Path $SrcDir 'developer_mode_final_acceptance_v1120.py'
$WrapperOut = Join-Path $SrcDir 'developer_mode_final_acceptance_rootfix_v1120.py'

$Artifact = Join-Path $TaskDir 'final_assets_v1070\originium_slug_x1c_bicolor_clean_v1070.gcode.3mf'
$AuditDir = Join-Path $TaskDir 'diagnostics_v1120'
$Audit = Join-Path $AuditDir 'm4_x1c_wire_mapping_v1120.json'

Write-Host "=== M4 X1C DEVELOPER MODE WIRE REBUILD V11.2.0 ===" -ForegroundColor Cyan
Write-Host "OFFLINE ONLY." -ForegroundColor Yellow
Write-Host "No printer connection. No MQTT. No FTPS. No print command."
Write-Host ""
Write-Host "Rebuild basis:"
Write-Host "  Backend base    : developer_mode_backend_v1000.py"
Write-Host "  Acceptance base : V10.6.2 (last branch that actually started the bicolor print)"
Write-Host "  Artifact        : V10.7 clean X1C-only sliced artifact"
Write-Host ""
Write-Host "Only wire correction:"
Write-Host "  input mapping [0,3]"
Write-Host "  -> X1C raw mapping [0,3,-1,-1,-1]"
Write-Host "  -> real JSON array (not JSON string)"
Write-Host "  -> NO ams_mapping2"
Write-Host "  -> NO ams_mapping_info"
Write-Host ""

foreach ($req in @($BackendBase, $AcceptanceBase, $WrapperBase, $Artifact)) {
    if (-not (Test-Path -LiteralPath $req -PathType Leaf)) {
        throw "Required file missing: $req"
    }
}

New-Item -ItemType Directory -Force -Path $AuditDir | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
foreach ($p in @($BackendOut, $AcceptanceOut, $WrapperOut)) {
    if (Test-Path -LiteralPath $p -PathType Leaf) {
        Copy-Item -LiteralPath $p -Destination "$p.pre_$stamp.bak" -Force
    }
}

$py = @'
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import re
import shutil
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
backend_base = pathlib.Path(sys.argv[2])
acceptance_base = pathlib.Path(sys.argv[3])
wrapper_base = pathlib.Path(sys.argv[4])
backend_out = pathlib.Path(sys.argv[5])
acceptance_out = pathlib.Path(sys.argv[6])
wrapper_out = pathlib.Path(sys.argv[7])
artifact = pathlib.Path(sys.argv[8])
audit_path = pathlib.Path(sys.argv[9])

WIRE = [0, 3, -1, -1, -1]

# ------------------------------------------------------------
# 1) Verify the clean sliced artifact is still exactly two logical tools.
# ------------------------------------------------------------
with zipfile.ZipFile(artifact, "r") as zf:
    names = set(zf.namelist())
    if "Metadata/plate_1.gcode" not in names:
        raise SystemExit("Artifact missing Metadata/plate_1.gcode")
    gcode = zf.read("Metadata/plate_1.gcode").decode("utf-8", "replace")

tools = sorted(
    {
        int(x)
        for x in re.findall(r"(?m)^\s*M620\s+S(\d+)", gcode)
        if int(x) != 255
    }
)
if tools != [0, 1]:
    raise SystemExit(f"Expected logical tools [0, 1], got {tools}")

# ------------------------------------------------------------
# 2) Rebuild backend from the actually installed current backend.
#    Patch ONLY mapping_value inside build_project_file_payload.
# ------------------------------------------------------------
backend_text = backend_base.read_text(encoding="utf-8")
tree = ast.parse(backend_text, filename=str(backend_base))

builder = None
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
        "build_project_file_payload",
        "_build_project_file_payload",
    }:
        builder = node
        break

if builder is None:
    raise SystemExit("Could not find project_file payload builder in backend.")

start = builder.lineno
end = getattr(builder, "end_lineno", None)
if end is None:
    raise SystemExit("Python AST did not expose builder end_lineno.")

lines = backend_text.splitlines(True)
builder_text = "".join(lines[start - 1 : end])

# Accept the exact historical states we actually created.
patterns = [
    '        mapping_value: Any = json.dumps(ams_mapping, separators=(",", ":"))\n',
    '        mapping_value: Any = json.dumps(ams_mapping, separators=(",", ":"), ensure_ascii=False)\n',
    '        mapping_value: Any = ams_mapping\n',
]

matches = [p for p in patterns if p in builder_text]
if len(matches) != 1:
    raise SystemExit(
        "Expected exactly one known mapping_value implementation inside builder; "
        f"found {len(matches)}. Refusing to guess."
    )

old = matches[0]
new = (
    '        # V11.2.0 X1/P1/A1 raw MQTT mapping rule.\n'
    '        # Keep project filament order; pad unused positions to legacy length 5.\n'
    '        # Emit a real JSON array via json.dumps(payload), NOT a nested JSON string.\n'
    '        if len(ams_mapping) > 5:\n'
    '            raise DeveloperBackendError("X1C AMS mapping cannot exceed 5 project positions.")\n'
    '        mapping_value: Any = list(ams_mapping) + [-1] * (5 - len(ams_mapping))\n'
)

patched_builder = builder_text.replace(old, new)
if patched_builder == builder_text:
    raise SystemExit("Backend mapping replacement made no change.")

backend_new = "".join(lines[: start - 1]) + patched_builder + "".join(lines[end:])

# Ensure we did not accidentally introduce H2-style fields.
builder_tree = ast.parse(patched_builder)
literal_keys = []
for n in ast.walk(builder_tree):
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        literal_keys.append(n.value)

if "ams_mapping2" in literal_keys or "ams_mapping_info" in literal_keys:
    raise SystemExit("Base project_file builder unexpectedly contains ams_mapping2/info.")

backend_out.write_text(backend_new, encoding="utf-8")

# ------------------------------------------------------------
# 3) Rebuild final acceptance from V10.6.2, NOT V10.7.
#    Therefore none of V10.7's mapping2/info injection survives.
# ------------------------------------------------------------
acc = acceptance_base.read_text(encoding="utf-8")

# Redirect backend import to V11.2 backend.
if "developer_mode_backend_v1000" not in acc:
    raise SystemExit("V10.6.2 acceptance does not reference developer_mode_backend_v1000.")
acc = acc.replace("developer_mode_backend_v1000", "developer_mode_backend_v1120")

# Version/evidence names only.
acc = acc.replace('"10.6.2"', '"11.2.0"')
acc = acc.replace('"10.6.0"', '"11.2.0"')
acc = acc.replace("nl-am-final-v1062-", "nl-am-final-v1120-")
acc = acc.replace("nl-am-final-v1060-", "nl-am-final-v1120-")
acc = acc.replace(
    "m4_developer_backend_final_live_events_v1062.jsonl",
    "m4_developer_backend_final_live_events_v1120.jsonl",
)
acc = acc.replace(
    "m4_developer_backend_final_live_events_v1060.jsonl",
    "m4_developer_backend_final_live_events_v1120.jsonl",
)
acc = acc.replace(
    "m4_developer_backend_final_acceptance_v1062.json",
    "m4_developer_backend_final_acceptance_v1120.json",
)
acc = acc.replace(
    "m4_developer_backend_final_acceptance_v1060.json",
    "m4_developer_backend_final_acceptance_v1120.json",
)

# V10.6.2 must not contain the later V10.7 injection.
for forbidden in ('_p["ams_mapping2"]', '_p["ams_mapping_info"]'):
    if forbidden in acc:
        raise SystemExit(f"Unexpected V10.7 AMS injection found in V10.6.2 base: {forbidden}")

acceptance_out.write_text(acc, encoding="utf-8")

# ------------------------------------------------------------
# 4) Reuse the proven root-FTPS wrapper, but point it at V11.2 modules.
#    The wrapper's FTPS implementation is preserved byte-for-byte otherwise.
# ------------------------------------------------------------
wrap = wrapper_base.read_text(encoding="utf-8")

if "developer_mode_backend_v1000" not in wrap:
    raise SystemExit("Rootfix wrapper does not reference developer_mode_backend_v1000.")
if "developer_mode_final_acceptance_v1070" not in wrap:
    raise SystemExit("Rootfix wrapper does not reference developer_mode_final_acceptance_v1070.")

wrap = wrap.replace("developer_mode_backend_v1000", "developer_mode_backend_v1120")
wrap = wrap.replace("developer_mode_final_acceptance_v1070", "developer_mode_final_acceptance_v1120")
wrapper_out.write_text(wrap, encoding="utf-8")

# ------------------------------------------------------------
# 5) Offline import + exact payload dry run against the NEW backend.
# ------------------------------------------------------------
sys.path.insert(0, str(root / "src"))
from am_print_executor import developer_mode_backend_v1120 as backend  # type: ignore

builder_fn = getattr(backend, "build_project_file_payload", None)
if builder_fn is None:
    builder_fn = getattr(backend, "_build_project_file_payload", None)
if builder_fn is None:
    raise SystemExit("V11.2 backend payload builder not importable.")

sig = inspect.signature(builder_fn)
kwargs = {}
for name, param in sig.parameters.items():
    if name == "sequence_id":
        kwargs[name] = "V1120_DRYRUN"
    elif name == "remote_path":
        # Use the root-path convention already proven by this project's X1C.
        kwargs[name] = "/v1120_dryrun.gcode.3mf"
    elif name == "gcode_entry":
        kwargs[name] = "Metadata/plate_1.gcode"
    elif name == "ams_mapping":
        kwargs[name] = [0, 3]
    elif name == "use_ams":
        kwargs[name] = True
    elif param.default is inspect._empty:
        raise SystemExit(f"Unknown required builder parameter: {name}")

payload = builder_fn(**kwargs)
if not isinstance(payload, dict):
    raise SystemExit("Payload builder did not return dict.")
pobj = payload.get("print")
if not isinstance(pobj, dict):
    raise SystemExit("Payload has no print object.")

actual = pobj.get("ams_mapping")

if actual != WIRE:
    raise SystemExit(f"Wrong V11.2 ams_mapping: {actual!r}")
if not isinstance(actual, list):
    raise SystemExit(f"V11.2 ams_mapping is not a real JSON list: {type(actual).__name__}")
if pobj.get("use_ams") is not True:
    raise SystemExit(f"use_ams is not true: {pobj.get('use_ams')!r}")
if "ams_mapping2" in pobj:
    raise SystemExit("V11.2 X1C payload unexpectedly contains ams_mapping2.")
if "ams_mapping_info" in pobj:
    raise SystemExit("V11.2 X1C payload unexpectedly contains ams_mapping_info.")

wire_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
roundtrip = json.loads(wire_json)
rt_mapping = roundtrip["print"]["ams_mapping"]

if rt_mapping != WIRE or not isinstance(rt_mapping, list):
    raise SystemExit("JSON serialization changed mapping type/value.")

audit = {
    "version": "11.2.0",
    "offline_only": True,
    "artifact": str(artifact),
    "logical_tools": tools,
    "backend_base": str(backend_base),
    "backend_out": str(backend_out),
    "acceptance_base": str(acceptance_base),
    "acceptance_out": str(acceptance_out),
    "wrapper_out": str(wrapper_out),
    "historical_mapping_implementation_replaced": old.strip(),
    "input_mapping": [0, 3],
    "wire_mapping": actual,
    "wire_mapping_python_type": type(actual).__name__,
    "wire_mapping_json_roundtrip_type": type(rt_mapping).__name__,
    "use_ams": pobj.get("use_ams"),
    "ams_mapping2_present": "ams_mapping2" in pobj,
    "ams_mapping_info_present": "ams_mapping_info" in pobj,
    "payload_print": pobj,
}

audit_path.parent.mkdir(parents=True, exist_ok=True)
audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

print("[PASS] Clean artifact logical tools = [0, 1].")
print(f"[PASS] Replaced historical backend mapping implementation: {old.strip()}")
print("[PASS] V11.2 backend input [0,3] -> wire [0,3,-1,-1,-1].")
print("[PASS] Wire ams_mapping is a REAL JSON ARRAY.")
print("[PASS] use_ams = true.")
print("[PASS] ams_mapping2 is absent.")
print("[PASS] ams_mapping_info is absent.")
print("[PASS] V11.2 acceptance rebuilt from V10.6.2, not V10.7.")
print("[PASS] JSON round-trip preserves list type.")
print("V1120_WIRE_DRYRUN=PASS")
'@

$tmp = Join-Path $env:TEMP ("m4_v1120_rebuild_" + [guid]::NewGuid().ToString("N") + ".py")
[System.IO.File]::WriteAllText(
    $tmp,
    $py,
    (New-Object System.Text.UTF8Encoding($false))
)

try {
    & python $tmp $Root $BackendBase $AcceptanceBase $WrapperBase `
        $BackendOut $AcceptanceOut $WrapperOut $Artifact $Audit
    $rc = $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}

if ($rc -ne 0) {
    Write-Host ""
    Write-Host "M4_X1C_WIRE_REBUILD_V1120=NOT_PASS" -ForegroundColor Red
    Write-Host "Exit code: $rc"
    Write-Host "No printer command was sent."
    exit $rc
}

Write-Host ""
Write-Host "=== PYTHON SYNTAX CHECK ===" -ForegroundColor Cyan
& python -m py_compile $BackendOut $AcceptanceOut $WrapperOut
if ($LASTEXITCODE -ne 0) {
    throw "V11.2.0 py_compile failed."
}
Write-Host "[PASS] V11.2 backend + acceptance + wrapper syntax valid." -ForegroundColor Green

Write-Host ""
Write-Host "=== RESULT ===" -ForegroundColor Cyan
Write-Host "M4_X1C_WIRE_REBUILD_V1120=PASS" -ForegroundColor Green
Write-Host "No printer command was sent."
Write-Host ""
Write-Host "Backend:"
Write-Host "  $BackendOut"
Write-Host "Acceptance:"
Write-Host "  $AcceptanceOut"
Write-Host "Wrapper:"
Write-Host "  $WrapperOut"
Write-Host "Audit:"
Write-Host "  $Audit"

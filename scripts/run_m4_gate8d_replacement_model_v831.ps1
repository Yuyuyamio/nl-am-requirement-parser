param(
    [string]$ProjectRoot = "",
    [int]$Epochs = 30,
    [int]$ImageSize = 224,
    [int]$Batch = 16
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = Join-Path "E:\" "nl-am-requirement-parser-M2-source-20260804_152224"
}

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "Project root not found: $ProjectRoot"
}

$TaskDir = Join-Path (Join-Path (Join-Path $ProjectRoot "outputs") "m4") "M2-1E4B2301FADD"
$Gate8CReport = Join-Path $TaskDir "m4_gate8c_model_screening_multiframe_v820.json"
$DatasetRoot = Join-Path $ProjectRoot "gate8c_dataset"
$SourceRoot = Join-Path (Join-Path $DatasetRoot "_source_cache_v5") "extracted"
$Gate8DRoot = Join-Path $DatasetRoot "gate8d_yolo26_v831"
$TrainScript = Join-Path $Gate8DRoot "_gate8d_train_v831.py"
$Report = Join-Path $TaskDir "m4_gate8d_replacement_model_candidate_v831.json"

if (-not (Test-Path -LiteralPath $Gate8CReport)) {
    throw "Gate8C report missing: $Gate8CReport"
}

if (-not (Test-Path -LiteralPath $SourceRoot)) {
    throw "V5 source dataset missing: $SourceRoot"
}

if (Test-Path -LiteralPath $Report) {
    throw "STOP: Gate8D report already exists; refusing to overwrite: $Report"
}

New-Item -ItemType Directory -Force -Path $Gate8DRoot | Out-Null

Write-Host ""
Write-Host "============================================"
Write-Host "M4 Gate8D v8.3.1"
Write-Host "Project-specific replacement model candidate"
Write-Host "============================================"
Write-Host "Model: YOLO26n classification transfer learning"
Write-Host "Dataset: verified 455-image V5 source"
Write-Host "Split: contiguous index blocks per class"
Write-Host "No printer / camera / MQTT / FTPS / control"
Write-Host ""

Write-Host "[1/6] Checking Python runtime..."

$OldEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"

& python --version
$PythonVersionExit = $LASTEXITCODE

$ErrorActionPreference = $OldEAP

if ($PythonVersionExit -ne 0) {
    throw "Python is not available from PATH."
}

Write-Host "[2/6] Checking Torch / Ultralytics import..."

$OldEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"

& python -c "import ultralytics, torch; print('ultralytics', ultralytics.__version__); print('torch', torch.__version__); print('cuda_available', torch.cuda.is_available())"
$ImportExit = $LASTEXITCODE

$ErrorActionPreference = $OldEAP

if ($ImportExit -ne 0) {
    Write-Host ""
    Write-Host "[INFO] Torch/Ultralytics import is missing or broken."
    Write-Host "[INFO] Installing/upgrading the official Ultralytics package..."

    $OldEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    & python -m pip install --disable-pip-version-check --upgrade pip
    $PipUpgradeExit = $LASTEXITCODE

    if ($PipUpgradeExit -ne 0) {
        Write-Host "[WARN] pip self-upgrade failed; continuing with the existing pip."
    }

    & python -m pip install --disable-pip-version-check -U ultralytics
    $InstallExit = $LASTEXITCODE

    $ErrorActionPreference = $OldEAP

    if ($InstallExit -ne 0) {
        throw "Failed to install/upgrade Ultralytics."
    }

    Write-Host ""
    Write-Host "[INFO] Verifying the repaired Python environment..."

    $OldEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    & python -c "import ultralytics, torch; print('ultralytics', ultralytics.__version__); print('torch', torch.__version__); print('cuda_available', torch.cuda.is_available()); from ultralytics import YOLO; print('YOLO_IMPORT_OK')"
    $VerifyExit = $LASTEXITCODE

    $ErrorActionPreference = $OldEAP

    if ($VerifyExit -ne 0) {
        throw "Ultralytics installation completed but import verification still failed. The full Python traceback above is the real dependency error."
    }
}

Write-Host "[OK] Python AI environment is ready."

$Python = @'
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import torch
from ultralytics import YOLO

project_root = Path(sys.argv[1]).resolve()
source_root = Path(sys.argv[2]).resolve()
dataset_root = Path(sys.argv[3]).resolve()
task_dir = Path(sys.argv[4]).resolve()
gate8c_report_path = Path(sys.argv[5]).resolve()
report_path = Path(sys.argv[6]).resolve()
epochs = int(sys.argv[7])
imgsz = int(sys.argv[8])
batch = int(sys.argv[9])

REQUEST_ID = "M2-1E4B2301FADD"

if not gate8c_report_path.is_file():
    raise SystemExit(f"Gate8C report missing: {gate8c_report_path}")

gate8c = json.loads(gate8c_report_path.read_text(encoding="utf-8-sig"))

if gate8c.get("version") != "8.2.0":
    raise SystemExit("Gate8C version lock failed.")

if gate8c.get("status") != "real_ai_screening_completed":
    raise SystemExit("Gate8C did not complete real AI screening.")

metrics8c = gate8c.get("model_screening_metrics") or {}
if int(metrics8c.get("total_sample_count", 0)) != 60:
    raise SystemExit("Gate8C baseline must contain the 60-image evaluation.")

# This Gate is a replacement-model candidate because the current model
# has not been accepted. We do not invent a production threshold here.
if gate8c.get("model_quality_status") != "NOT_ACCEPTED_YET_REVIEW_METRICS":
    raise SystemExit("Unexpected Gate8C model quality status.")

exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
images = sorted(
    [p for p in source_root.rglob("*") if p.is_file() and p.suffix.lower() in exts],
    key=lambda p: str(p).lower(),
)

if len(images) != 455:
    raise SystemExit(f"Expected exactly 455 V5 source images, found {len(images)}.")

def parse_class_and_index(path: Path):
    stem = path.stem.strip()
    m = re.match(r"^(.*?)[_-](\d+)$", stem)
    if not m:
        raise SystemExit(f"Cannot parse source filename: {path.name}")

    raw_class = m.group(1).strip()
    index = int(m.group(2))

    canonical = {
        "normal": "normal",
        "bolhas": "bubbles",
        "bubbles": "bubbles",
        "overextrusion": "overextrusion",
        "overextrusion10": "overextrusion10",
        "overextrusion40": "overextrusion40",
    }.get(raw_class.lower())

    if canonical is None:
        raise SystemExit(f"Unexpected source class {raw_class!r} from {path.name}")

    return canonical, index, raw_class

by_class = defaultdict(list)
raw_class_counts = Counter()
hash_owner = {}

for p in images:
    canonical, idx, raw = parse_class_and_index(p)
    raw_class_counts[raw] += 1

    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    if digest in hash_owner:
        raise SystemExit(
            f"Exact duplicate image detected: {p} duplicates {hash_owner[digest]}"
        )
    hash_owner[digest] = str(p)

    by_class[canonical].append((idx, p, digest))

expected_counts = {
    "normal": 91,
    "bubbles": 91,
    "overextrusion": 91,
    "overextrusion10": 90,
    "overextrusion40": 92,
}

actual_counts = {k: len(v) for k, v in by_class.items()}
if actual_counts != expected_counts:
    raise SystemExit(
        "V5 source class counts changed. "
        f"Expected {expected_counts}, got {actual_counts}"
    )

# Avoid random frame leakage. Each class is ordered by its source image index,
# then split into contiguous blocks.
# 70% train, 15% val, remaining 15% test.
split_root = dataset_root / "dataset"
if split_root.exists():
    shutil.rmtree(split_root)

split_manifest = []
split_counts = defaultdict(Counter)

for class_name, items in sorted(by_class.items()):
    ordered = sorted(items, key=lambda x: x[0])

    n = len(ordered)
    n_train = math.floor(n * 0.70)
    n_val = math.floor(n * 0.15)

    blocks = {
        "train": ordered[:n_train],
        "val": ordered[n_train:n_train + n_val],
        "test": ordered[n_train + n_val:],
    }

    for split_name, block in blocks.items():
        dest_dir = split_root / split_name / class_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        for idx, src, digest in block:
            dst = dest_dir / src.name
            shutil.copy2(src, dst)

            copied_hash = hashlib.sha256(dst.read_bytes()).hexdigest()
            if copied_hash != digest:
                raise SystemExit(f"SHA256 mismatch after copying {src.name}")

            split_counts[split_name][class_name] += 1
            split_manifest.append({
                "split": split_name,
                "class": class_name,
                "source_index": idx,
                "source_file": str(src),
                "dataset_file": str(dst),
                "sha256": digest,
            })

# Verify no hash occurs across more than one split.
hash_to_splits = defaultdict(set)
for row in split_manifest:
    hash_to_splits[row["sha256"]].add(row["split"])

cross_split_duplicates = {
    h: sorted(v) for h, v in hash_to_splits.items() if len(v) > 1
}
if cross_split_duplicates:
    raise SystemExit("Exact duplicate leakage across splits detected.")

print("")
print("Gate8D split counts")
print("-------------------")
for split in ("train", "val", "test"):
    total = sum(split_counts[split].values())
    print(split, "total =", total, dict(split_counts[split]))

device = 0 if torch.cuda.is_available() else "cpu"
device_label = (
    f"cuda:{torch.cuda.get_device_name(0)}"
    if torch.cuda.is_available()
    else "cpu"
)

print("")
print("Training device =", device_label)
print("Base model      = yolo26n-cls.pt")
print("Epochs          =", epochs)
print("Image size      =", imgsz)
print("Batch           =", batch)
print("")

run_project = task_dir / "gate8d_yolo26_v831_runs"
run_name = "train"

model = YOLO("yolo26n-cls.pt")

train_results = model.train(
    data=str(split_root),
    epochs=epochs,
    imgsz=imgsz,
    batch=batch,
    workers=0,
    device=device,
    project=str(run_project),
    name=run_name,
    exist_ok=False,
    seed=42,
    deterministic=True,
    patience=8,
    verbose=True,
)

save_dir = Path(train_results.save_dir).resolve()
best_pt = save_dir / "weights" / "best.pt"

if not best_pt.is_file():
    raise SystemExit(f"Training finished but best.pt is missing: {best_pt}")

best_model = YOLO(str(best_pt))

class_names = ["bubbles", "normal", "overextrusion", "overextrusion10", "overextrusion40"]
# Read class-name order from the trained model; don't assume alphabetical order.
names_map = best_model.names
if isinstance(names_map, dict):
    index_to_name = {int(k): str(v) for k, v in names_map.items()}
else:
    index_to_name = {i: str(v) for i, v in enumerate(names_map)}

test_rows = []
confusion = defaultdict(Counter)

test_dir = split_root / "test"
test_files = sorted(
    [p for p in test_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts],
    key=lambda p: str(p).lower(),
)

for path in test_files:
    true_class = path.parent.name

    pred_result = best_model.predict(
        source=str(path),
        imgsz=imgsz,
        device=device,
        verbose=False,
    )[0]

    probs = pred_result.probs
    if probs is None:
        raise SystemExit(f"No classification probabilities for {path}")

    top1 = int(probs.top1)
    pred_class = index_to_name[top1]
    top1conf = float(probs.top1conf.item())

    confusion[true_class][pred_class] += 1

    true_binary = "success" if true_class == "normal" else "failure"
    pred_binary = "success" if pred_class == "normal" else "failure"

    test_rows.append({
        "image_file": str(path),
        "true_class": true_class,
        "predicted_class": pred_class,
        "confidence": top1conf,
        "true_binary": true_binary,
        "predicted_binary": pred_binary,
        "correct_multiclass": pred_class == true_class,
        "correct_binary": pred_binary == true_binary,
    })

if not test_rows:
    raise SystemExit("Test split is empty.")

multi_correct = sum(r["correct_multiclass"] for r in test_rows)
binary_correct = sum(r["correct_binary"] for r in test_rows)

true_success = [r for r in test_rows if r["true_binary"] == "success"]
true_failure = [r for r in test_rows if r["true_binary"] == "failure"]

success_recall = (
    sum(r["predicted_binary"] == "success" for r in true_success) / len(true_success)
)
failure_recall = (
    sum(r["predicted_binary"] == "failure" for r in true_failure) / len(true_failure)
)

binary_accuracy = binary_correct / len(test_rows)
balanced_accuracy = (success_recall + failure_recall) / 2.0
multiclass_accuracy = multi_correct / len(test_rows)

baseline_balanced = float(
    metrics8c.get("balanced_accuracy_unknown_counted_wrong", 0.0)
)
baseline_failure_recall = float(metrics8c.get("failure_recall", 0.0))
baseline_success_recall = float(metrics8c.get("success_recall", 0.0))

delta_balanced = balanced_accuracy - baseline_balanced
delta_failure = failure_recall - baseline_failure_recall
delta_success = success_recall - baseline_success_recall

report = {
    "schema_version": "0.1.0",
    "module": "M4",
    "phase": "8D",
    "version": "8.3.1",
    "stage": "project_specific_replacement_model_candidate_env_recovery",
    "request_id": REQUEST_ID,
    "status": "replacement_model_candidate_evaluated",
    "created_unix": time.time(),
    "source_gate8c": {
        "report_file": str(gate8c_report_path),
        "status": gate8c.get("status"),
        "model_quality_status": gate8c.get("model_quality_status"),
        "printguard_baseline": {
            "success_recall": baseline_success_recall,
            "failure_recall": baseline_failure_recall,
            "balanced_accuracy": baseline_balanced,
        },
    },
    "dataset": {
        "source_root": str(source_root),
        "total_images": len(images),
        "source_class_counts": actual_counts,
        "source_note": (
            "Dataset images span the manufacturing process. "
            "Gate8D therefore uses contiguous source-index blocks, "
            "not a random image split."
        ),
        "split_rule": {
            "train": "first 70 percent by numeric source index within each class",
            "val": "next 15 percent by numeric source index within each class",
            "test": "remaining 15 percent by numeric source index within each class",
            "exact_hash_leakage_across_splits": False,
        },
        "split_counts": {
            split: dict(split_counts[split])
            for split in ("train", "val", "test")
        },
        "split_manifest": split_manifest,
    },
    "model": {
        "framework": "Ultralytics",
        "base_model": "yolo26n-cls.pt",
        "task": "5-class image classification",
        "classes": index_to_name,
        "training_device": device_label,
        "training_parameters": {
            "epochs_requested": epochs,
            "imgsz": imgsz,
            "batch": batch,
            "workers": 0,
            "seed": 42,
            "deterministic": True,
            "patience": 8,
        },
        "best_weights": str(best_pt),
        "training_run_directory": str(save_dir),
    },
    "held_out_test": {
        "sample_count": len(test_rows),
        "multiclass_accuracy": multiclass_accuracy,
        "binary_success_failure": {
            "accuracy": binary_accuracy,
            "success_recall": success_recall,
            "failure_recall": failure_recall,
            "balanced_accuracy": balanced_accuracy,
        },
        "delta_vs_printguard_gate8c": {
            "success_recall": delta_success,
            "failure_recall": delta_failure,
            "balanced_accuracy": delta_balanced,
        },
        "confusion_matrix_nested_counts": {
            true_cls: dict(pred_counts)
            for true_cls, pred_counts in confusion.items()
        },
        "predictions": test_rows,
    },
    "candidate_quality_status": "CANDIDATE_METRICS_READY_FOR_REVIEW",
    "quality_policy": {
        "production_acceptance_threshold_used": False,
        "x1c_camera_generalization_claimed": False,
        "reason": (
            "This dataset is small and contains process-sequence images. "
            "Held-out metrics screen the replacement candidate but do not "
            "establish production or X1C-camera generalization."
        ),
    },
    "policy": {
        "office_only": True,
        "printer_connection_attempted": False,
        "camera_connection_attempted": False,
        "mqtt_publish_count": 0,
        "ftps_connection_attempted": False,
        "printer_control_count": 0,
        "gcode_modified": False,
        "bambu_profile_modified": False,
    },
    "next_phase": (
        "review_gate8d_candidate_metrics_then_export_adapter_or_reject_candidate"
    ),
}

report_path.write_text(
    json.dumps(report, ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("")
print("============================================")
print("GATE8D CANDIDATE EVALUATED")
print("============================================")
print("test samples          =", len(test_rows))
print("multiclass accuracy   =", round(multiclass_accuracy, 6))
print("binary accuracy       =", round(binary_accuracy, 6))
print("success recall        =", round(success_recall, 6))
print("failure recall        =", round(failure_recall, 6))
print("balanced accuracy     =", round(balanced_accuracy, 6))
print("delta balanced vs PG  =", round(delta_balanced, 6))
print("best weights          =", best_pt)
print("report                =", report_path)
'@

$Utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($TrainScript, $Python, $Utf8)

Write-Host "[3/6] Preparing dataset split..."
Write-Host "[4/6] Training replacement candidate..."
Write-Host "[5/6] Running held-out evaluation..."

$OldEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"

& python `
    $TrainScript `
    $ProjectRoot `
    $SourceRoot `
    $Gate8DRoot `
    $TaskDir `
    $Gate8CReport `
    $Report `
    $Epochs `
    $ImageSize `
    $Batch

$TrainExit = $LASTEXITCODE
$ErrorActionPreference = $OldEAP

if ($TrainExit -ne 0) {
    throw "Gate8D training/evaluation failed. The Python traceback printed immediately above is the real error."
}

Remove-Item -LiteralPath $TrainScript -Force -ErrorAction SilentlyContinue

Write-Host "[6/6] Gate8D report created."
Write-Host "Report:"
Write-Host "  $Report"
Write-Host ""
Write-Host "No physical printer action occurred."

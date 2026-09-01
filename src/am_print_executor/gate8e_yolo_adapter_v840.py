from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ultralytics import YOLO

EXPECTED_CLASSES = {"normal","bubbles","overextrusion","overextrusion10","overextrusion40"}

class Gate8EAdapterError(RuntimeError):
    pass

@dataclass
class Gate8EOutput:
    raw_model_class: str
    confidence: float
    binary_prediction: str
    vision_risk: str
    recommended_action: str
    defect_type: str | None
    human_review_required: bool
    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_model_class": self.raw_model_class,
            "confidence": self.confidence,
            "binary_prediction": self.binary_prediction,
            "vision_risk": self.vision_risk,
            "recommended_action": self.recommended_action,
            "defect_type": self.defect_type,
            "human_review_required": self.human_review_required,
        }

class Gate8EYOLOAdapter:
    def __init__(self, weights: str | Path, *, device: str | int | None = None, imgsz: int = 224):
        self.weights = Path(weights).resolve()
        if not self.weights.is_file():
            raise Gate8EAdapterError(f"Model weights missing: {self.weights}")
        self.device = device
        self.imgsz = int(imgsz)
        self.model = YOLO(str(self.weights))
        names = self.model.names
        if isinstance(names, dict):
            self.index_to_name = {int(k): str(v) for k, v in names.items()}
        else:
            self.index_to_name = {i: str(v) for i, v in enumerate(names)}
        actual = set(self.index_to_name.values())
        if actual != EXPECTED_CLASSES:
            raise Gate8EAdapterError(
                f"Unexpected trained classes. Expected {sorted(EXPECTED_CLASSES)}, got {sorted(actual)}"
            )

    @staticmethod
    def map_class(raw_model_class: str, confidence: float) -> Gate8EOutput:
        raw = str(raw_model_class).strip().lower()
        conf = float(confidence)
        if raw == "normal":
            return Gate8EOutput(raw, conf, "success", "normal", "CONTINUE", None, False)
        if raw in EXPECTED_CLASSES - {"normal"}:
            return Gate8EOutput(raw, conf, "failure", "attention", "REVIEW", None, True)
        raise Gate8EAdapterError(f"Unsupported model class: {raw!r}")

    def classify(self, image_path: str | Path) -> dict[str, Any]:
        image = Path(image_path).resolve()
        if not image.is_file():
            raise Gate8EAdapterError(f"Image missing: {image}")
        kwargs: dict[str, Any] = {"source": str(image), "imgsz": self.imgsz, "verbose": False}
        if self.device is not None:
            kwargs["device"] = self.device
        result = self.model.predict(**kwargs)[0]
        if result.probs is None:
            raise Gate8EAdapterError(f"Classification probabilities missing for: {image}")
        top1 = int(result.probs.top1)
        confidence = float(result.probs.top1conf.item())
        raw_class = self.index_to_name[top1]
        mapped = self.map_class(raw_class, confidence).as_dict()
        mapped["image_file"] = str(image)
        return mapped

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from am_model_generator.contracts import (
    M2InputError,
)
from am_model_generator.intake import (
    load_m1_input,
)


def _write_json(
    path: Path,
    data: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _creative_payload() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_type": "creative_asset",
        "intent_summary": "生成一只可3D打印的坐姿卡通小狗",
        "object_name": "cartoon puppy",
        "category": "animal",
        "visual_description": "一只坐着的卡通小狗，整体造型圆润可爱",
        "style": "cartoon",
        "pose": "sitting",
        "target_height_mm": 100.0,
        "target_dimensions_text": "height 100 mm",
        "output_target": "3d_print",
        "generation_prompt_en": (
            "A cute sitting cartoon puppy, "
            "rounded forms, printable 3D model, "
            "100 mm tall."
        ),
        "negative_prompt_en": (
            "floating parts, disconnected geometry, "
            "extremely thin fragile features"
        ),
        "system_printability_guidance": [
            "watertight closed mesh",
            "single connected main body",
            "flat and stable contact surface",
            "no floating geometry",
            "avoid extremely thin fragile parts",
        ],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.95,
    }


def _engineering_payload() -> dict[str, Any]:
    return {
        "schema_version": "0.2.0",
        "request_id": (
            "REQ-ENGINEERING-001"
        ),
        "status": "complete",
        "original_input": {
            "input_type": "text",
            "raw_text": (
                "设计一个80毫米长、40毫米宽、"
                "8毫米高的安装底板。"
            ),
        },
        "product": {
            "name": "mounting plate",
            "functions": [
                "component mounting",
            ],
            "application_environment": [
                "indoor",
            ],
        },
        "geometry": {
            "design_domain": {
                "shape": (
                    "rectangular_prism"
                ),
                "dimensions": {
                    "length": {
                        "value": 80.0,
                        "unit": "mm",
                    },
                    "width": {
                        "value": 40.0,
                        "unit": "mm",
                    },
                    "height": {
                        "value": 8.0,
                        "unit": "mm",
                    },
                },
                "source_type": "user",
                "source_text": (
                    "80毫米长、40毫米宽、"
                    "8毫米高"
                ),
                "confirmation_status": (
                    "confirmed"
                ),
            },
            "preserved_regions": [],
            "forbidden_regions": [],
            "interfaces": [],
            "symmetry": None,
            "reference_frame": "global",
        },
        "materials": [],
        "boundary_conditions": {
            "fixed_regions": [],
            "loads": [],
            "thermal_conditions": [],
            "contacts": [],
        },
        "performance_requirements": {},
        "optimization": {
            "objectives": [],
            "hard_constraints": [],
            "soft_preferences": [],
        },
        "additive_manufacturing": {
            "process": "FDM",
            "machine": None,
            "build_direction": [
                0,
                0,
                1,
            ],
            "minimum_wall_thickness": {
                "value": 3.0,
                "unit": "mm",
            },
            "minimum_feature_size": {
                "value": 1.0,
                "unit": "mm",
            },
            "maximum_overhang": {
                "value": 45.0,
                "unit": "degree",
            },
            "support_policy": "allowed",
            "material_removal": "none",
        },
        "assumptions": [],
        "derived_constraints": [],
        "missing_information": [],
        "conflicts": [],
        "clarification_questions": [],
        "source_evidence": [],
        "validation": {
            "schema_valid": True,
            "unit_valid": True,
            "engineering_complete": True,
            "manufacturing_feasible": True,
            "errors": [],
        },
        "approval": {
            "engineer_confirmed": False,
            "confirmed_by": None,
            "confirmed_at": None,
        },
    }


def _manifest(
    *,
    task_type: str,
    status: str,
    output_file: str,
    next_module: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M1",
        "original_input": (
            "test input"
        ),
        "task_type": task_type,
        "status": status,
        "route_file": "task_route.json",
        "output_file": output_file,
        "next_module": next_module,
    }


class TestM2Intake(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary_directory.name
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_accepts_ready_creative_input(
        self,
    ) -> None:
        payload_path = (
            self.root
            / "creative_asset_spec.json"
        )

        manifest_path = (
            self.root
            / "m1_manifest.json"
        )

        _write_json(
            payload_path,
            _creative_payload(),
        )

        _write_json(
            manifest_path,
            _manifest(
                task_type="creative_asset",
                status="ready",
                output_file=payload_path.name,
                next_module="M2",
            ),
        )

        loaded = load_m1_input(
            manifest_path
        )

        self.assertEqual(
            loaded.task_type,
            "creative_asset",
        )

        self.assertEqual(
            loaded.payload_path,
            payload_path.resolve(),
        )

        self.assertEqual(
            loaded.payload[
                "target_height_mm"
            ],
            100.0,
        )

    def test_accepts_complete_engineering_input(
        self,
    ) -> None:
        payload_path = (
            self.root
            / "requirement_spec.json"
        )

        manifest_path = (
            self.root
            / "m1_manifest.json"
        )

        _write_json(
            payload_path,
            _engineering_payload(),
        )

        _write_json(
            manifest_path,
            _manifest(
                task_type=(
                    "engineering_part"
                ),
                status="complete",
                output_file=payload_path.name,
                next_module="M2",
            ),
        )

        loaded = load_m1_input(
            manifest_path
        )

        self.assertEqual(
            loaded.task_type,
            "engineering_part",
        )

        self.assertEqual(
            loaded.request_id,
            "REQ-ENGINEERING-001",
        )

    def test_rejects_incomplete_input(
        self,
    ) -> None:
        manifest_path = (
            self.root
            / "m1_manifest.json"
        )

        _write_json(
            manifest_path,
            _manifest(
                task_type=(
                    "engineering_part"
                ),
                status="incomplete",
                output_file=(
                    "requirement_spec.json"
                ),
                next_module=None,
            ),
        )

        with self.assertRaises(
            M2InputError
        ) as context:
            load_m1_input(
                manifest_path
            )

        self.assertEqual(
            context.exception.code,
            "M2_INPUT_NOT_READY",
        )

    def test_rejects_missing_payload(
        self,
    ) -> None:
        manifest_path = (
            self.root
            / "m1_manifest.json"
        )

        _write_json(
            manifest_path,
            _manifest(
                task_type="creative_asset",
                status="ready",
                output_file=(
                    "missing_spec.json"
                ),
                next_module="M2",
            ),
        )

        with self.assertRaises(
            M2InputError
        ) as context:
            load_m1_input(
                manifest_path
            )

        self.assertEqual(
            context.exception.code,
            "M2_PAYLOAD_NOT_FOUND",
        )

    def test_rejects_invalid_manifest_json(
        self,
    ) -> None:
        manifest_path = (
            self.root
            / "m1_manifest.json"
        )

        manifest_path.write_text(
            "{ invalid json",
            encoding="utf-8",
        )

        with self.assertRaises(
            M2InputError
        ) as context:
            load_m1_input(
                manifest_path
            )

        self.assertEqual(
            context.exception.code,
            "M2_MANIFEST_INVALID_JSON",
        )


if __name__ == "__main__":
    unittest.main()
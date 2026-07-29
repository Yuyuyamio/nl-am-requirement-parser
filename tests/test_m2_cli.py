from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from am_model_generator.cli import main
from am_model_generator.contracts import (
    M2PlanningError,
    validate_m2_manifest,
    validate_m2_request,
    validate_m2_route,
)
from am_model_generator.planning import (
    plan_m2,
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
        "intent_summary": (
            "生成一只可3D打印的坐姿卡通小狗"
        ),
        "object_name": "cartoon puppy",
        "category": "animal",
        "visual_description": (
            "一只坐着的卡通小狗，"
            "整体造型圆润可爱"
        ),
        "style": "cartoon",
        "pose": "sitting",
        "target_height_mm": 100.0,
        "target_dimensions_text": (
            "height 100 mm"
        ),
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


def _ready_manifest(
    output_file: str,
) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "module": "M1",
        "original_input": (
            "给我打印一只坐着的卡通小狗，"
            "高度10厘米"
        ),
        "task_type": "creative_asset",
        "status": "ready",
        "route_file": "task_route.json",
        "output_file": output_file,
        "next_module": "M2",
    }


def _create_ready_m1(
    root: Path,
) -> Path:
    m1_directory = (
        root
        / "outputs"
        / "m1"
    )

    payload_path = (
        m1_directory
        / "creative_asset_spec.json"
    )

    manifest_path = (
        m1_directory
        / "m1_manifest.json"
    )

    _write_json(
        payload_path,
        _creative_payload(),
    )

    _write_json(
        manifest_path,
        _ready_manifest(
            payload_path.name
        ),
    )

    return manifest_path


class TestM2CLI(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary_directory.name
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_plan_creates_three_files(
        self,
    ) -> None:
        manifest_path = (
            _create_ready_m1(
                self.root
            )
        )

        output_root = (
            self.root
            / "outputs"
            / "m2"
        )

        result = plan_m2(
            manifest_path,
            output_root=output_root,
        )

        output_directory = Path(
            result["output_directory"]
        )

        self.assertTrue(
            output_directory.is_dir()
        )

        self.assertFalse(
            result[
                "reused_existing_plan"
            ]
        )

        route_data = json.loads(
            (
                output_directory
                / "m2_route.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        request_data = json.loads(
            (
                output_directory
                / "m2_request.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        manifest_data = json.loads(
            (
                output_directory
                / "m2_manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            validate_m2_route(
                route_data
            ),
            [],
        )

        self.assertEqual(
            validate_m2_request(
                request_data
            ),
            [],
        )

        self.assertEqual(
            validate_m2_manifest(
                manifest_data
            ),
            [],
        )

    def test_second_run_reuses_plan(
        self,
    ) -> None:
        manifest_path = (
            _create_ready_m1(
                self.root
            )
        )

        output_root = (
            self.root
            / "outputs"
            / "m2"
        )

        first = plan_m2(
            manifest_path,
            output_root=output_root,
        )

        second = plan_m2(
            manifest_path,
            output_root=output_root,
        )

        self.assertFalse(
            first[
                "reused_existing_plan"
            ]
        )

        self.assertTrue(
            second[
                "reused_existing_plan"
            ]
        )

        self.assertEqual(
            first["output_directory"],
            second["output_directory"],
        )

    def test_conflicting_plan_is_rejected(
        self,
    ) -> None:
        manifest_path = (
            _create_ready_m1(
                self.root
            )
        )

        output_root = (
            self.root
            / "outputs"
            / "m2"
        )

        result = plan_m2(
            manifest_path,
            output_root=output_root,
        )

        route_path = (
            Path(
                result[
                    "output_directory"
                ]
            )
            / "m2_route.json"
        )

        route_data = json.loads(
            route_path.read_text(
                encoding="utf-8"
            )
        )

        route_data["request_id"] = (
            "BROKEN-REQUEST-ID"
        )

        _write_json(
            route_path,
            route_data,
        )

        with self.assertRaises(
            M2PlanningError
        ) as context:
            plan_m2(
                manifest_path,
                output_root=(
                    output_root
                ),
            )

        self.assertEqual(
            context.exception.code,
            "M2_OUTPUT_CONFLICT",
        )

    def test_cli_returns_success_json(
        self,
    ) -> None:
        manifest_path = (
            _create_ready_m1(
                self.root
            )
        )

        output_root = (
            self.root
            / "outputs"
            / "m2"
        )

        output = io.StringIO()

        with redirect_stdout(output):
            return_code = main(
                [
                    str(manifest_path),
                    "--output-dir",
                    str(output_root),
                ]
            )

        self.assertEqual(
            return_code,
            0,
        )

        result = json.loads(
            output.getvalue()
        )

        self.assertEqual(
            result["status"],
            "planned",
        )

        self.assertTrue(
            Path(
                result[
                    "manifest_file"
                ]
            ).is_file()
        )

    def test_cli_rejects_incomplete_m1(
        self,
    ) -> None:
        m1_directory = (
            self.root
            / "outputs"
            / "m1"
        )

        manifest_path = (
            m1_directory
            / "m1_manifest.json"
        )

        _write_json(
            manifest_path,
            {
                "schema_version": "0.1.0",
                "module": "M1",
                "original_input": (
                    "incomplete test"
                ),
                "task_type": (
                    "engineering_part"
                ),
                "status": "incomplete",
                "route_file": (
                    "task_route.json"
                ),
                "output_file": (
                    "requirement_spec.json"
                ),
                "next_module": None,
            },
        )

        output = io.StringIO()

        with redirect_stdout(output):
            return_code = main(
                [
                    str(manifest_path),
                    "--output-dir",
                    str(
                        self.root
                        / "outputs"
                        / "m2"
                    ),
                ]
            )

        self.assertEqual(
            return_code,
            1,
        )

        result = json.loads(
            output.getvalue()
        )

        self.assertEqual(
            result["error_code"],
            "M2_INPUT_NOT_READY",
        )


if __name__ == "__main__":
    unittest.main()
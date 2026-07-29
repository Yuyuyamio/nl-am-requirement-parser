from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from am_model_generator.artifacts import (
    acquire_m2_artifact,
)
from am_model_generator.contracts import (
    M2ProviderError,
)
from am_model_generator.mesh_validation import (
    validate_m2_mesh,
    validate_mesh_report,
)
from am_model_generator.planning import (
    plan_m2,
)
from am_model_generator.status import (
    poll_m2_task,
)
from am_model_generator.submission import (
    submit_m2_plan,
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
            "生成高度200毫米的恐怖老鼠"
        ),
        "object_name": (
            "Super Large Scary Mouse"
        ),
        "category": "animal",
        "visual_description": (
            "A large terrifying mouse."
        ),
        "style": None,
        "pose": None,
        "target_height_mm": 200.0,
        "target_dimensions_text": (
            "高度200毫米"
        ),
        "output_target": "3d_print",
        "generation_prompt_en": (
            "Create a large terrifying mouse "
            "as a printable 3D model, "
            "200 mm tall."
        ),
        "negative_prompt_en": (
            "floating geometry, open mesh"
        ),
        "system_printability_guidance": [
            "watertight closed mesh",
            "single connected main body"
        ],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.8
    }


def _create_local_model_task(
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
        {
            "schema_version": "0.1.0",
            "module": "M1",
            "original_input": (
                "给我打印一只超级大的"
                "恐怖老鼠，高度20厘米"
            ),
            "task_type": "creative_asset",
            "status": "ready",
            "route_file": (
                "task_route.json"
            ),
            "output_file": (
                payload_path.name
            ),
            "next_module": "M2",
        },
    )

    plan_result = plan_m2(
        manifest_path,
        output_root=(
            root
            / "outputs"
            / "m2"
        ),
    )

    task_directory = Path(
        plan_result["output_directory"]
    )

    submit_m2_plan(
        task_directory
    )

    poll_m2_task(
        task_directory
    )

    acquire_m2_artifact(
        task_directory
    )

    return task_directory


class TestM2MeshValidation(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary_directory.name
        )

        self.task_directory = (
            _create_local_model_task(
                self.root
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_mock_cube_passes_validation(
        self,
    ) -> None:
        result = validate_m2_mesh(
            self.task_directory
        )

        self.assertTrue(
            result[
                "hard_constraints_passed"
            ]
        )

        self.assertFalse(
            result[
                "reused_existing_validation"
            ]
        )

        report = result["report"]

        self.assertEqual(
            validate_mesh_report(
                report
            ),
            [],
        )

        self.assertGreater(
            report["vertex_count"],
            0,
        )

        self.assertGreater(
            report["face_count"],
            0,
        )

        self.assertEqual(
            report[
                "connected_components"
            ],
            1,
        )

        self.assertTrue(
            report["watertight"]
        )

        self.assertTrue(
            report[
                "winding_consistent"
            ]
        )

        self.assertEqual(
            report["extents_mm"],
            {
                "x": 20.0,
                "y": 20.0,
                "z": 20.0,
            },
        )

        self.assertEqual(
            report[
                "scale_factor_to_target_height"
            ],
            10.0,
        )

        self.assertIn(
            "model_height_requires_normalization",
            report["warnings"],
        )

    def test_second_validation_is_reused(
        self,
    ) -> None:
        first = validate_m2_mesh(
            self.task_directory
        )

        second = validate_m2_mesh(
            self.task_directory
        )

        self.assertFalse(
            first[
                "reused_existing_validation"
            ]
        )

        self.assertTrue(
            second[
                "reused_existing_validation"
            ]
        )

        self.assertEqual(
            first["report"],
            second["report"],
        )

    def test_manifest_is_updated(
        self,
    ) -> None:
        validate_m2_mesh(
            self.task_directory
        )

        manifest = json.loads(
            (
                self.task_directory
                / "m2_manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest[
                "validation_file"
            ],
            "mesh_validation.json",
        )

        self.assertTrue(
            manifest[
                "hard_constraints_passed"
            ]
        )

        self.assertEqual(
            manifest["status"],
            "generated",
        )

    def test_modified_model_is_rejected(
        self,
    ) -> None:
        model_path = (
            self.task_directory
            / "raw_model.glb"
        )

        with model_path.open("ab") as file:
            file.write(
                b"BROKEN"
            )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            validate_m2_mesh(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            (
                "M2_MESH_ARTIFACT_HASH_MISMATCH"
            ),
        )

    def test_missing_model_is_rejected(
        self,
    ) -> None:
        (
            self.task_directory
            / "raw_model.glb"
        ).unlink()

        with self.assertRaises(
            M2ProviderError
        ) as context:
            validate_m2_mesh(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            "M2_MESH_MODEL_FILE_MISSING",
        )


if __name__ == "__main__":
    unittest.main()
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
)
from am_model_generator.normalization import (
    normalize_m2_model,
)
from am_model_generator.normalized_validation import (
    validate_normalized_m2_mesh,
    validate_normalized_mesh_report,
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
            "生成高度201毫米的恐怖老鼠"
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
        "target_height_mm": 201.0,
        "target_dimensions_text": (
            "高度201毫米"
        ),
        "output_target": "3d_print",
        "generation_prompt_en": (
            "Create a large terrifying mouse "
            "as a printable 3D model, "
            "201 mm tall."
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


def _create_normalized_task(
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
                "恐怖老鼠，高度201毫米"
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
        plan_result[
            "output_directory"
        ]
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

    validate_m2_mesh(
        task_directory
    )

    normalize_m2_model(
        task_directory
    )

    return task_directory


class TestM2NormalizedValidation(
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
            _create_normalized_task(
                self.root
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_normalized_model_passes_validation(
        self,
    ) -> None:
        result = (
            validate_normalized_m2_mesh(
                self.task_directory
            )
        )

        self.assertTrue(
            result[
                "hard_constraints_passed"
            ]
        )

        self.assertTrue(
            result[
                "height_within_tolerance"
            ]
        )

        self.assertAlmostEqual(
            result[
                "actual_height_mm"
            ],
            201.0,
            places=5,
        )

        self.assertFalse(
            result[
                "reused_existing_validation"
            ]
        )

        report = result["report"]

        self.assertEqual(
            validate_normalized_mesh_report(
                report
            ),
            [],
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
                "x": 201.0,
                "y": 201.0,
                "z": 201.0,
            },
        )

        self.assertEqual(
            report[
                "hard_checks"
            ][
                "target_height_match"
            ],
            True,
        )

    def test_second_validation_is_reused(
        self,
    ) -> None:
        first = (
            validate_normalized_m2_mesh(
                self.task_directory
            )
        )

        second = (
            validate_normalized_m2_mesh(
                self.task_directory
            )
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
        validate_normalized_m2_mesh(
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
            manifest["primary_model"],
            "normalized_model.glb",
        )

        self.assertEqual(
            manifest[
                "validation_file"
            ],
            (
                "normalized_mesh_validation.json"
            ),
        )

        self.assertTrue(
            manifest[
                "hard_constraints_passed"
            ]
        )

    def test_modified_normalized_model_is_rejected(
        self,
    ) -> None:
        model_path = (
            self.task_directory
            / "normalized_model.glb"
        )

        with model_path.open(
            "ab"
        ) as file:
            file.write(
                b"BROKEN"
            )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            validate_normalized_m2_mesh(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            (
                "M2_NORMALIZED_VALIDATION_MODEL_CHANGED"
            ),
        )


if __name__ == "__main__":
    unittest.main()
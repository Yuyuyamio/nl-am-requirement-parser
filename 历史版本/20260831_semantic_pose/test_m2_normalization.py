from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import trimesh

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
    validate_normalization_receipt,
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


def _sha256_file(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


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


def _create_validated_raw_task(
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

    validate_m2_mesh(
        task_directory
    )

    return task_directory


class TestM2Normalization(
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
            _create_validated_raw_task(
                self.root
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_normalization_creates_target_height_model(
        self,
    ) -> None:
        raw_model_path = (
            self.task_directory
            / "raw_model.glb"
        )

        raw_hash_before = _sha256_file(
            raw_model_path
        )

        result = normalize_m2_model(
            self.task_directory
        )

        self.assertFalse(
            result[
                "reused_existing_normalization"
            ]
        )

        self.assertEqual(
            result["source_height_mm"],
            20.0,
        )

        self.assertEqual(
            result["target_height_mm"],
            201.0,
        )

        self.assertEqual(
            result["scale_factor"],
            10.05,
        )

        normalized_path = (
            self.task_directory
            / "normalized_model.glb"
        )

        self.assertTrue(
            normalized_path.is_file()
        )

        scene = trimesh.load_scene(
            normalized_path,
            process=False,
        )

        if hasattr(
            scene,
            "to_mesh",
        ):
            mesh = scene.to_mesh()
        else:
            mesh = scene.dump(
                concatenate=True
            )

        self.assertAlmostEqual(
            float(mesh.extents[2]),
            201.0,
            places=5,
        )

        self.assertAlmostEqual(
            float(mesh.bounds[0][2]),
            0.0,
            places=5,
        )

        self.assertEqual(
            _sha256_file(
                raw_model_path
            ),
            raw_hash_before,
        )

        receipt = json.loads(
            (
                self.task_directory
                / "normalization_receipt.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            validate_normalization_receipt(
                receipt
            ),
            [],
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

        self.assertIsNone(
            manifest["validation_file"]
        )

        self.assertIsNone(
            manifest[
                "hard_constraints_passed"
            ]
        )

    def test_second_normalization_is_reused(
        self,
    ) -> None:
        first = normalize_m2_model(
            self.task_directory
        )

        second = normalize_m2_model(
            self.task_directory
        )

        self.assertFalse(
            first[
                "reused_existing_normalization"
            ]
        )

        self.assertTrue(
            second[
                "reused_existing_normalization"
            ]
        )

        self.assertEqual(
            first[
                "normalized_model_sha256"
            ],
            second[
                "normalized_model_sha256"
            ],
        )

    def test_modified_source_model_is_rejected(
        self,
    ) -> None:
        raw_model_path = (
            self.task_directory
            / "raw_model.glb"
        )

        with raw_model_path.open(
            "ab"
        ) as file:
            file.write(
                b"BROKEN"
            )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            normalize_m2_model(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            (
                "M2_NORMALIZATION_SOURCE_CHANGED"
            ),
        )

    def test_incomplete_normalization_state_is_rejected(
        self,
    ) -> None:
        (
            self.task_directory
            / "normalized_model.glb"
        ).write_bytes(
            b"temporary"
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            normalize_m2_model(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            (
                "M2_NORMALIZATION_STATE_INCOMPLETE"
            ),
        )


if __name__ == "__main__":
    unittest.main()
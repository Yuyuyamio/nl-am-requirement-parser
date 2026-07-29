from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from am_model_generator.artifacts import (
    acquire_m2_artifact,
    validate_artifact_receipt,
)
from am_model_generator.contracts import (
    M2ProviderError,
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


def _create_generated_task(
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

    return task_directory


class TestM2Artifacts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary_directory.name
        )

        self.task_directory = (
            _create_generated_task(
                self.root
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_acquire_creates_valid_local_glb(
        self,
    ) -> None:
        result = acquire_m2_artifact(
            self.task_directory
        )

        self.assertFalse(
            result[
                "reused_existing_artifact"
            ]
        )

        self.assertFalse(
            result["network_called"]
        )

        model_path = (
            self.task_directory
            / "raw_model.glb"
        )

        receipt_path = (
            self.task_directory
            / "artifact_receipt.json"
        )

        self.assertTrue(
            model_path.is_file()
        )

        self.assertEqual(
            model_path.read_bytes()[:4],
            b"glTF",
        )

        receipt = json.loads(
            receipt_path.read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            validate_artifact_receipt(
                receipt
            ),
            [],
        )

        self.assertEqual(
            receipt["sha256"],
            _sha256_file(model_path),
        )

        self.assertEqual(
            receipt["size_bytes"],
            model_path.stat().st_size,
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
            manifest["status"],
            "generated",
        )

        self.assertEqual(
            manifest["primary_model"],
            "raw_model.glb",
        )

    def test_second_acquire_is_idempotent(
        self,
    ) -> None:
        first = acquire_m2_artifact(
            self.task_directory
        )

        second = acquire_m2_artifact(
            self.task_directory
        )

        self.assertFalse(
            first[
                "reused_existing_artifact"
            ]
        )

        self.assertTrue(
            second[
                "reused_existing_artifact"
            ]
        )

        self.assertEqual(
            first["sha256"],
            second["sha256"],
        )

    def test_modified_model_is_rejected(
        self,
    ) -> None:
        acquire_m2_artifact(
            self.task_directory
        )

        model_path = (
            self.task_directory
            / "raw_model.glb"
        )

        with model_path.open("ab") as file:
            file.write(b"BROKEN")

        with self.assertRaises(
            M2ProviderError
        ) as context:
            acquire_m2_artifact(
                self.task_directory
            )

        self.assertIn(
            context.exception.code,
            {
                "M2_ARTIFACT_GLB_LENGTH_MISMATCH",
                "M2_ARTIFACT_FILE_CHANGED",
            },
        )

    def test_incomplete_local_state_is_rejected(
        self,
    ) -> None:
        (
            self.task_directory
            / "raw_model.glb"
        ).write_bytes(b"glTF")

        with self.assertRaises(
            M2ProviderError
        ) as context:
            acquire_m2_artifact(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            "M2_ARTIFACT_STATE_INCOMPLETE",
        )


if __name__ == "__main__":
    unittest.main()
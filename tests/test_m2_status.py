from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

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


def _creative_payload() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_type": "creative_asset",
        "intent_summary": (
            "生成一只高度200毫米的恐怖老鼠"
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
            "floating geometry, open mesh, "
            "disconnected parts"
        ),
        "system_printability_guidance": [
            "watertight closed mesh",
            "single connected main body",
            "flat and stable contact surface"
        ],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.8
    }


def _create_submitted_task(
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

    return task_directory


class TestM2Status(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary_directory.name
        )

        self.task_directory = (
            _create_submitted_task(
                self.root
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_poll_completes_mock_task(
        self,
    ) -> None:
        result = poll_m2_task(
            self.task_directory
        )

        self.assertEqual(
            result["status"],
            "generated",
        )

        self.assertEqual(
            result["provider_status"],
            "completed",
        )

        self.assertTrue(
            result["status_changed"]
        )

        self.assertFalse(
            result["network_called"]
        )

        self.assertEqual(
            len(result["artifacts"]),
            1,
        )

        self.assertTrue(
            result["artifacts"][0].startswith(
                "mock://"
            )
        )

        manifest_data = json.loads(
            (
                self.task_directory
                / "m2_manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest_data["status"],
            "generated",
        )

        self.assertEqual(
            manifest_data["provider"],
            "mock",
        )

        self.assertIsNone(
            manifest_data["primary_model"]
        )

        self.assertIsNone(
            manifest_data["next_module"]
        )

    def test_second_poll_is_idempotent(
        self,
    ) -> None:
        first = poll_m2_task(
            self.task_directory
        )

        second = poll_m2_task(
            self.task_directory
        )

        self.assertTrue(
            first["status_changed"]
        )

        self.assertFalse(
            second["status_changed"]
        )

        self.assertEqual(
            first["provider_job_id"],
            second["provider_job_id"],
        )

        self.assertEqual(
            first["artifacts"],
            second["artifacts"],
        )

    def test_provider_mismatch_is_rejected(
        self,
    ) -> None:
        with self.assertRaises(
            M2ProviderError
        ) as context:
            poll_m2_task(
                self.task_directory,
                provider_name="meshy",
            )

        self.assertEqual(
            context.exception.code,
            "M2_STATUS_PROVIDER_MISMATCH",
        )

    def test_missing_submission_is_rejected(
        self,
    ) -> None:
        (
            self.task_directory
            / "provider_submission.json"
        ).unlink()

        with self.assertRaises(
            M2ProviderError
        ) as context:
            poll_m2_task(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            "M2_STATUS_FILE_MISSING",
        )

    def test_request_id_mismatch_is_rejected(
        self,
    ) -> None:
        submission_path = (
            self.task_directory
            / "provider_submission.json"
        )

        submission_data = json.loads(
            submission_path.read_text(
                encoding="utf-8"
            )
        )

        submission_data["request_id"] = (
            "BROKEN-REQUEST-ID"
        )

        _write_json(
            submission_path,
            submission_data,
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            poll_m2_task(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            "M2_STATUS_REQUEST_ID_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
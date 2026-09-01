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
from am_model_generator.providers import (
    validate_provider_request,
    validate_provider_submission,
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


def _create_m1_manifest(
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

    return manifest_path


class TestM2Submission(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )

        self.root = Path(
            self.temporary_directory.name
        )

        self.manifest_path = (
            _create_m1_manifest(
                self.root
            )
        )

        plan_result = plan_m2(
            self.manifest_path,
            output_root=(
                self.root
                / "outputs"
                / "m2"
            ),
        )

        self.task_directory = Path(
            plan_result[
                "output_directory"
            ]
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_submit_creates_provider_files(
        self,
    ) -> None:
        result = submit_m2_plan(
            self.task_directory
        )

        self.assertFalse(
            result[
                "reused_existing_submission"
            ]
        )

        self.assertFalse(
            result["network_called"]
        )

        request_data = json.loads(
            (
                self.task_directory
                / "provider_request.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        submission_data = json.loads(
            (
                self.task_directory
                / "provider_submission.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            validate_provider_request(
                request_data
            ),
            [],
        )

        self.assertEqual(
            validate_provider_submission(
                submission_data
            ),
            [],
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
            "generating",
        )

        self.assertEqual(
            manifest_data["provider"],
            "mock",
        )

        self.assertIsNone(
            manifest_data["primary_model"]
        )

    def test_second_run_reuses_submission(
        self,
    ) -> None:
        first = submit_m2_plan(
            self.task_directory
        )

        second = submit_m2_plan(
            self.task_directory
        )

        self.assertFalse(
            first[
                "reused_existing_submission"
            ]
        )

        self.assertTrue(
            second[
                "reused_existing_submission"
            ]
        )

        self.assertEqual(
            first["provider_job_id"],
            second["provider_job_id"],
        )

    def test_changed_source_spec_is_rejected(
        self,
    ) -> None:
        source_path = (
            self.root
            / "outputs"
            / "m1"
            / "creative_asset_spec.json"
        )

        payload = json.loads(
            source_path.read_text(
                encoding="utf-8"
            )
        )

        payload[
            "generation_prompt_en"
        ] += " Add larger ears."

        _write_json(
            source_path,
            payload,
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            submit_m2_plan(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            "M2_SOURCE_SPEC_CHANGED",
        )

    def test_incomplete_submission_state_is_rejected(
        self,
    ) -> None:
        _write_json(
            (
                self.task_directory
                / "provider_request.json"
            ),
            {},
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            submit_m2_plan(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            (
                "M2_SUBMISSION_STATE_INCOMPLETE"
            ),
        )

    def test_modified_provider_request_is_rejected(
        self,
    ) -> None:
        submit_m2_plan(
            self.task_directory
        )

        request_path = (
            self.task_directory
            / "provider_request.json"
        )

        request_data = json.loads(
            request_path.read_text(
                encoding="utf-8"
            )
        )

        request_data["prompt"] += (
            " Modified outside the system."
        )

        _write_json(
            request_path,
            request_data,
        )

        with self.assertRaises(
            M2ProviderError
        ) as context:
            submit_m2_plan(
                self.task_directory
            )

        self.assertEqual(
            context.exception.code,
            "M2_PROVIDER_REQUEST_CONFLICT",
        )


if __name__ == "__main__":
    unittest.main()
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import trimesh

from am_model_generator.contracts import M2ProviderError
from am_model_generator.providers.base import CreativeGenerationRequest
from am_model_generator.providers.triposg_local import TripoSGLocalProvider
from am_model_generator.wsl_bridge import WslWorkerResult


class TripoSGLocalProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)

        self.cache_root = (
            Path(self.temporary_directory.name)
            / "cache"
        )
        self.bridge = Mock()
        self.provider = TripoSGLocalProvider(
            bridge=self.bridge,
            cache_root=self.cache_root,
            wsl_cache_root="/mnt/e/test-cache",
            linux_home="/home/fishcan",
        )
        self.request = CreativeGenerationRequest(
            schema_version="0.1.0",
            request_id="request-001",
            route="creative_mesh",
            provider="triposg_local",
            idempotency_key="idempotency-key-001",
            source_payload_sha256="a" * 64,
            prompt="a cute sitting cartoon dog",
            negative_prompt=None,
            target_height_mm=60.0,
            metadata={},
        )

    @staticmethod
    def _worker_result(payload) -> WslWorkerResult:
        return WslWorkerResult(
            command=("wsl.exe",),
            payload=payload,
            stdout="{}",
            stderr="",
            return_code=0,
        )

    def _worker_side_effect(
        self,
        *,
        environment_name,
        script_path,
        arguments,
        timeout_seconds=None,
    ):
        del script_path
        del timeout_seconds

        output_index = (
            arguments.index("--output-path")
            + 1
        )
        output_path = arguments[output_index]

        job_directories = list(
            self.cache_root.glob(
                "triposg-local-*"
            )
        )
        self.assertEqual(
            len(job_directories),
            1,
        )
        job_directory = job_directories[0]

        if environment_name == "t2i":
            (
                job_directory
                / "reference_image.png"
            ).write_bytes(b"fake-png")
            return self._worker_result(
                {
                    "status": "completed",
                    "output_image": output_path,
                    "reused_existing": False,
                }
            )

        if environment_name == "triposg":
            normalized_mesh = (
                trimesh.creation.box(
                    extents=(
                        1.403456,
                        1.819824,
                        1.874882,
                    )
                )
            )
            (
                job_directory
                / "generated_model.glb"
            ).write_bytes(
                trimesh.exchange.gltf.export_glb(
                    trimesh.Scene(
                        normalized_mesh
                    )
                )
            )
            return self._worker_result(
                {
                    "status": "completed",
                    "output_model": output_path,
                    "geometry": {
                        "geometry_count": 1,
                        "vertex_count": 100,
                        "face_count": 200,
                    },
                    "reused_existing": False,
                }
            )

        self.fail(
            f"unexpected environment: {environment_name}"
        )

    def test_submit_rescales_normalized_mesh_and_completes(
        self,
    ) -> None:
        self.bridge.run_json_worker.side_effect = (
            self._worker_side_effect
        )

        submission = self.provider.submit(
            self.request
        )

        self.assertEqual(
            submission.status,
            "completed",
        )
        self.assertEqual(
            submission.provider,
            "triposg_local",
        )
        self.assertEqual(
            len(submission.artifacts),
            1,
        )
        self.assertTrue(
            submission.artifacts[0].startswith(
                "triposg-local://"
            )
        )

        calls = (
            self.bridge
            .run_json_worker
            .call_args_list
        )
        self.assertEqual(
            len(calls),
            2,
        )
        self.assertEqual(
            calls[0].kwargs["environment_name"],
            "t2i",
        )
        self.assertEqual(
            calls[1].kwargs["environment_name"],
            "triposg",
        )
        self.assertFalse(
            submission.provider_metadata[
                "network_called"
            ]
        )

        raw_mesh = trimesh.load_mesh(
            submission.provider_metadata[
                "raw_generated_model_path"
            ],
            process=False,
        )
        physical_mesh = trimesh.load_mesh(
            submission.provider_metadata[
                "generated_model_path"
            ],
            process=False,
        )
        raw_extents = [
            float(value)
            for value in raw_mesh.extents
        ]
        physical_extents = [
            float(value)
            for value in physical_mesh.extents
        ]

        self.assertTrue(
            math.isclose(
                raw_extents[2],
                1.874882,
                abs_tol=1e-6,
            )
        )
        self.assertTrue(
            math.isclose(
                physical_extents[2],
                60.0,
                abs_tol=0.1,
            )
        )
        self.assertGreater(
            min(physical_extents),
            10.0,
        )
        self.assertTrue(
            math.isclose(
                physical_extents[0]
                / physical_extents[2],
                raw_extents[0]
                / raw_extents[2],
                rel_tol=1e-6,
                abs_tol=1e-6,
            )
        )
        self.assertTrue(
            math.isclose(
                physical_extents[1]
                / physical_extents[2],
                raw_extents[1]
                / raw_extents[2],
                rel_tol=1e-6,
                abs_tol=1e-6,
            )
        )

    def test_second_submit_reuses_in_memory_submission(
        self,
    ) -> None:
        self.bridge.run_json_worker.side_effect = (
            self._worker_side_effect
        )

        first = self.provider.submit(
            self.request
        )
        second = self.provider.submit(
            self.request
        )

        self.assertFalse(
            first.reused_existing_submission
        )
        self.assertTrue(
            second.reused_existing_submission
        )
        self.assertEqual(
            self.bridge
            .run_json_worker
            .call_count,
            2,
        )

    def test_download_artifact_copies_cached_glb(
        self,
    ) -> None:
        self.bridge.run_json_worker.side_effect = (
            self._worker_side_effect
        )

        submission = self.provider.submit(
            self.request
        )
        destination = (
            Path(self.temporary_directory.name)
            / "download"
            / "raw_model.glb"
        )

        metadata = self.provider.download_artifact(
            submission.artifacts[0],
            destination,
        )

        downloaded_mesh = trimesh.load_mesh(
            destination,
            process=False,
        )
        self.assertTrue(
            math.isclose(
                float(
                    downloaded_mesh.extents[2]
                ),
                60.0,
                abs_tol=0.1,
            )
        )
        self.assertFalse(
            metadata["network_called"]
        )
        self.assertEqual(
            metadata["artifact_format"],
            "glb",
        )

    def test_rejects_provider_mismatch(
        self,
    ) -> None:
        wrong_request = CreativeGenerationRequest(
            schema_version="0.1.0",
            request_id="request-002",
            route="creative_mesh",
            provider="mock",
            idempotency_key="idempotency-key-002",
            source_payload_sha256="b" * 64,
            prompt="test object",
            negative_prompt=None,
            target_height_mm=None,
            metadata={},
        )

        with self.assertRaises(
            M2ProviderError
        ) as captured:
            self.provider.submit(
                wrong_request
            )

        self.assertEqual(
            captured.exception.code,
            "M2_PROVIDER_REQUEST_MISMATCH",
        )
        self.assertEqual(
            self.bridge
            .run_json_worker
            .call_count,
            0,
        )

    def test_rejects_invalid_artifact_uri(
        self,
    ) -> None:
        destination = (
            Path(self.temporary_directory.name)
            / "raw_model.glb"
        )

        with self.assertRaises(
            M2ProviderError
        ) as captured:
            self.provider.download_artifact(
                "file:///tmp/model.glb",
                destination,
            )

        self.assertEqual(
            captured.exception.code,
            (
                "M2_TRIPOSG_LOCAL_"
                "ARTIFACT_URI_INVALID"
            ),
        )


if __name__ == "__main__":
    unittest.main()

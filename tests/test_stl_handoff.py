from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

import trimesh

from am_model_generator.mesh_validation import (
    validate_m2_mesh,
)
from am_model_generator.normalization import (
    normalize_m2_model,
)
from am_model_generator.stl_handoff import (
    create_m2_stl_handoff,
)


def sha256_file(
    path: Path,
) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def write_json(
    path: Path,
    data,
) -> None:
    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class StlOnlyHandoffTests(
    unittest.TestCase
):
    def setUp(self) -> None:
        self.temporary = (
            tempfile.TemporaryDirectory()
        )
        self.addCleanup(
            self.temporary.cleanup
        )

        self.task = Path(
            self.temporary.name
        )

        box = trimesh.creation.box(
            extents=(20.0, 30.0, 40.0)
        )
        raw_path = (
            self.task / "raw_model.glb"
        )
        raw_path.write_bytes(
            trimesh.exchange.gltf.export_glb(
                trimesh.Scene(box)
            )
        )
        raw_sha256 = sha256_file(
            raw_path
        )

        request_id = (
            "stl-only-handoff-test"
        )

        write_json(
            self.task
            / "provider_request.json",
            {
                "schema_version": "0.1.0",
                "request_id": request_id,
                "route": "creative_mesh",
                "provider": "triposg_local",
                "idempotency_key": (
                    "M2SUB-0123456789ABCDEF0123"
                ),
                "source_payload_sha256": (
                    "a" * 64
                ),
                "prompt": "test box",
                "negative_prompt": None,
                "target_height_mm": 100.0,
                "metadata": {},
            },
        )

        write_json(
            self.task
            / "m2_manifest.json",
            {
                "schema_version": "0.1.0",
                "module": "M2",
                "request_id": request_id,
                "task_type": "creative_asset",
                "status": "generated",
                "source_m1_manifest": (
                    "m1_manifest.json"
                ),
                "source_spec": (
                    "creative_asset_spec.json"
                ),
                "route_file": (
                    "m2_route.json"
                ),
                "request_file": (
                    "m2_request.json"
                ),
                "generator_route": (
                    "creative_mesh"
                ),
                "provider": "triposg_local",
                "primary_model": (
                    "raw_model.glb"
                ),
                "validation_file": None,
                "hard_constraints_passed": None,
                "next_module": None,
            },
        )

        write_json(
            self.task
            / "artifact_receipt.json",
            {
                "schema_version": "0.1.0",
                "module": "M2",
                "request_id": request_id,
                "provider": "triposg_local",
                "provider_job_id": (
                    "triposg-local-test"
                ),
                "source_uri": (
                    "triposg-local://test/model.glb"
                ),
                "local_file": "raw_model.glb",
                "media_type": (
                    "model/gltf-binary"
                ),
                "sha256": raw_sha256,
                "size_bytes": (
                    raw_path.stat().st_size
                ),
                "status": "available",
                "provider_metadata": {
                    "network_called": False,
                },
            },
        )

        raw_validation = (
            validate_m2_mesh(self.task)
        )
        self.assertTrue(
            raw_validation[
                "hard_constraints_passed"
            ]
        )

        normalize_m2_model(
            self.task
        )

        normalized_validation = (
            validate_m2_mesh(self.task)
        )
        self.assertTrue(
            normalized_validation[
                "hard_constraints_passed"
            ]
        )

        self.normalized_path = (
            self.task
            / "normalized_model.glb"
        )
        self.normalized_hash = (
            sha256_file(
                self.normalized_path
            )
        )

    def test_stl_is_the_only_manifest_model_for_m3(
        self,
    ) -> None:
        result = create_m2_stl_handoff(
            self.task
        )

        self.assertEqual(
            result["status"],
            "stl_handoff_ready",
        )
        self.assertEqual(
            result["m3_model_format"],
            "stl",
        )

        stl_path = (
            self.task / "m2_output.stl"
        )
        validation_path = (
            self.task
            / "stl_validation.json"
        )
        handoff_path = (
            self.task
            / "m2_stl_handoff.json"
        )

        self.assertTrue(stl_path.is_file())
        self.assertTrue(
            validation_path.is_file()
        )
        self.assertTrue(
            handoff_path.is_file()
        )

        scene = trimesh.load_scene(
            stl_path,
            process=True,
        )

        if hasattr(scene, "to_mesh"):
            mesh = scene.to_mesh()
        else:
            mesh = scene.dump(
                concatenate=True
            )

        self.assertTrue(
            mesh.is_watertight
        )
        self.assertTrue(
            mesh.is_winding_consistent
        )
        self.assertTrue(
            math.isclose(
                float(mesh.extents[2]),
                100.0,
                abs_tol=1e-4,
            )
        )
        self.assertTrue(
            all(
                float(value) >= -1e-5
                for value in mesh.bounds[0]
            )
        )

        validation = json.loads(
            validation_path.read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(
            validation[
                "hard_constraints_passed"
            ]
        )
        self.assertEqual(
            validation["format"],
            "stl",
        )

        handoff = json.loads(
            handoff_path.read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            handoff["m3_model_file"],
            "m2_output.stl",
        )
        self.assertEqual(
            handoff["m3_model_format"],
            "stl",
        )

        manifest = json.loads(
            (
                self.task
                / "m2_manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest["status"],
            "complete",
        )
        self.assertEqual(
            manifest["primary_model"],
            "m2_output.stl",
        )
        self.assertEqual(
            manifest["validation_file"],
            "stl_validation.json",
        )
        self.assertTrue(
            manifest[
                "hard_constraints_passed"
            ]
        )
        self.assertEqual(
            manifest["next_module"],
            "M3",
        )

        self.assertEqual(
            sha256_file(
                self.normalized_path
            ),
            self.normalized_hash,
        )

        second = create_m2_stl_handoff(
            self.task
        )

        self.assertTrue(
            second[
                "reused_existing_handoff"
            ]
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from am_model_generator.mesh_repair import repair_m2_mesh
from am_model_generator.mesh_validation import validate_m2_mesh


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class MeshRepairPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.task = Path(self.temporary.name)

        box = trimesh.creation.box(extents=(20.0, 20.0, 20.0))
        mask = np.ones(len(box.faces), dtype=bool)
        mask[0] = False
        box.update_faces(mask)
        box.remove_unreferenced_vertices()

        self.raw_path = self.task / "raw_model.glb"
        self.raw_path.write_bytes(
            trimesh.exchange.gltf.export_glb(trimesh.Scene(box))
        )
        self.raw_sha256 = sha256_file(self.raw_path)
        request_id = "repair-test-request"

        write_json(
            self.task / "provider_request.json",
            {
                "schema_version": "0.1.0",
                "request_id": request_id,
                "route": "creative_mesh",
                "provider": "triposg_local",
                "idempotency_key": "M2SUB-0123456789ABCDEF0123",
                "source_payload_sha256": "a" * 64,
                "prompt": "test mesh",
                "negative_prompt": None,
                "target_height_mm": 100.0,
                "metadata": {},
            },
        )

        write_json(
            self.task / "m2_manifest.json",
            {
                "schema_version": "0.1.0",
                "module": "M2",
                "request_id": request_id,
                "task_type": "creative_asset",
                "status": "generated",
                "source_m1_manifest": "m1_manifest.json",
                "source_spec": "creative_asset_spec.json",
                "route_file": "m2_route.json",
                "request_file": "m2_request.json",
                "generator_route": "creative_mesh",
                "provider": "triposg_local",
                "primary_model": "raw_model.glb",
                "validation_file": None,
                "hard_constraints_passed": None,
                "next_module": None,
            },
        )

        write_json(
            self.task / "artifact_receipt.json",
            {
                "schema_version": "0.1.0",
                "module": "M2",
                "request_id": request_id,
                "provider": "triposg_local",
                "provider_job_id": "triposg-local-test",
                "source_uri": "triposg-local://test/model.glb",
                "local_file": "raw_model.glb",
                "media_type": "model/gltf-binary",
                "sha256": self.raw_sha256,
                "size_bytes": self.raw_path.stat().st_size,
                "status": "available",
                "provider_metadata": {"network_called": False},
            },
        )

    def test_repair_and_revalidate_preserves_raw_artifact(self) -> None:
        raw_validation = validate_m2_mesh(self.task)
        self.assertFalse(raw_validation["hard_constraints_passed"])

        repair = repair_m2_mesh(self.task, voxel_resolution=64)
        self.assertEqual(repair["status"], "repaired")
        self.assertEqual(sha256_file(self.raw_path), self.raw_sha256)

        repaired_path = self.task / "repaired_model.glb"
        self.assertTrue(repaired_path.is_file())

        repaired_validation = validate_m2_mesh(self.task)
        self.assertTrue(repaired_validation["hard_constraints_passed"])
        self.assertEqual(
            Path(repaired_validation["validation_file"]).name,
            "repaired_mesh_validation.json",
        )

        report = repaired_validation["report"]
        self.assertEqual(report["model_file"], "repaired_model.glb")
        self.assertTrue(report["watertight"])
        self.assertTrue(report["winding_consistent"])
        self.assertEqual(report["connected_components"], 1)

        manifest = json.loads(
            (self.task / "m2_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["primary_model"], "repaired_model.glb")
        self.assertEqual(
            manifest["validation_file"],
            "repaired_mesh_validation.json",
        )
        self.assertTrue(manifest["hard_constraints_passed"])


if __name__ == "__main__":
    unittest.main()

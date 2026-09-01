from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from am_model_generator.mesh_repair import (
    repair_m2_mesh,
)
from am_model_generator.mesh_validation import (
    validate_m2_mesh,
)
from am_model_generator.normalization import (
    normalize_m2_model,
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


def create_task(
    task: Path,
    *,
    open_mesh: bool,
) -> tuple[
    Path,
    str,
]:
    box = trimesh.creation.box(
        extents=(20.0, 20.0, 20.0)
    )

    if open_mesh:
        mask = np.ones(
            len(box.faces),
            dtype=bool,
        )
        mask[0] = False
        box.update_faces(mask)
        box.remove_unreferenced_vertices()

    raw_path = task / "raw_model.glb"

    raw_path.write_bytes(
        trimesh.exchange.gltf.export_glb(
            trimesh.Scene(box)
        )
    )

    raw_sha256 = sha256_file(raw_path)
    request_id = (
        "normalization-test-request"
    )

    write_json(
        task / "provider_request.json",
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
            "prompt": "test mesh",
            "negative_prompt": None,
            "target_height_mm": 100.0,
            "metadata": {},
        },
    )

    write_json(
        task / "m2_manifest.json",
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
            "route_file": "m2_route.json",
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
        task / "artifact_receipt.json",
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

    return raw_path, raw_sha256


class NormalizationPipelineTests(
    unittest.TestCase
):
    def test_repaired_model_normalizes_and_revalidates(
        self,
    ) -> None:
        temporary = (
            tempfile.TemporaryDirectory()
        )
        self.addCleanup(
            temporary.cleanup
        )

        task = Path(temporary.name)
        raw_path, raw_sha256 = (
            create_task(
                task,
                open_mesh=True,
            )
        )

        raw_validation = (
            validate_m2_mesh(task)
        )

        self.assertFalse(
            raw_validation[
                "hard_constraints_passed"
            ]
        )

        repair_m2_mesh(
            task,
            voxel_resolution=64,
        )

        repaired_validation = (
            validate_m2_mesh(task)
        )

        self.assertTrue(
            repaired_validation[
                "hard_constraints_passed"
            ]
        )

        repaired_path = (
            task / "repaired_model.glb"
        )
        repaired_sha256 = (
            sha256_file(repaired_path)
        )

        normalization = (
            normalize_m2_model(task)
        )

        self.assertEqual(
            normalization["status"],
            "normalized",
        )
        self.assertEqual(
            normalization["source_model"],
            "repaired_model.glb",
        )
        self.assertTrue(
            math.isclose(
                normalization[
                    "target_height_mm"
                ],
                100.0,
                abs_tol=1e-6,
            )
        )

        self.assertEqual(
            sha256_file(raw_path),
            raw_sha256,
        )
        self.assertEqual(
            sha256_file(repaired_path),
            repaired_sha256,
        )

        normalized_validation = (
            validate_m2_mesh(task)
        )

        self.assertTrue(
            normalized_validation[
                "hard_constraints_passed"
            ]
        )
        self.assertEqual(
            Path(
                normalized_validation[
                    "validation_file"
                ]
            ).name,
            "normalized_mesh_validation.json",
        )

        report = (
            normalized_validation[
                "report"
            ]
        )

        self.assertEqual(
            report["model_file"],
            "normalized_model.glb",
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
            report[
                "connected_components"
            ],
            1,
        )
        self.assertTrue(
            math.isclose(
                report[
                    "extents_mm"
                ]["z"],
                100.0,
                abs_tol=1e-5,
            )
        )
        self.assertTrue(
            math.isclose(
                report[
                    "scale_factor_to_target_height"
                ],
                1.0,
                abs_tol=1e-5,
            )
        )

        scene = trimesh.load_scene(
            task / "normalized_model.glb",
            process=False,
        )

        if hasattr(scene, "to_mesh"):
            mesh = scene.to_mesh()
        else:
            mesh = scene.dump(
                concatenate=True
            )

        self.assertTrue(
            math.isclose(
                float(mesh.bounds[0][1]),  # Serialized GLB is Y-up; manufacturing STL is Z-up.
                0.0,
                abs_tol=1e-6,
            )
        )

        second_normalization = (
            normalize_m2_model(task)
        )
        second_validation = (
            validate_m2_mesh(task)
        )

        self.assertTrue(
            second_normalization[
                "reused_existing_normalization"
            ]
        )
        self.assertTrue(
            second_validation[
                "reused_existing_validation"
            ]
        )

        manifest = json.loads(
            (
                task / "m2_manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest["primary_model"],
            "normalized_model.glb",
        )
        self.assertEqual(
            manifest["validation_file"],
            "normalized_mesh_validation.json",
        )
        self.assertTrue(
            manifest[
                "hard_constraints_passed"
            ]
        )

    def test_valid_raw_model_can_normalize_directly(
        self,
    ) -> None:
        temporary = (
            tempfile.TemporaryDirectory()
        )
        self.addCleanup(
            temporary.cleanup
        )

        task = Path(temporary.name)

        create_task(
            task,
            open_mesh=False,
        )

        raw_validation = (
            validate_m2_mesh(task)
        )

        self.assertTrue(
            raw_validation[
                "hard_constraints_passed"
            ]
        )

        normalization = (
            normalize_m2_model(task)
        )

        self.assertEqual(
            normalization["source_model"],
            "raw_model.glb",
        )

        normalized_validation = (
            validate_m2_mesh(task)
        )

        self.assertTrue(
            normalized_validation[
                "hard_constraints_passed"
            ]
        )


if __name__ == "__main__":
    unittest.main()

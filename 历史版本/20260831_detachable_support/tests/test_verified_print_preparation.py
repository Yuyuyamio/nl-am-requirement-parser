from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import trimesh

from am_print_executor.flat_base_gate import inspect_flat_printing_base
from am_print_executor.general_printability_geometry_repair import build_printability_repair_request
from am_print_executor.integrated_support_ribs import add_integrated_support_ribs
from am_print_executor.printability_blocker_clustering import cluster_gate_blockers
from am_print_executor.printable_foundation import add_printable_foundation
from am_print_executor.slice_geometry_frame import gate_report_in_geometry_frame, verify_source_matches_slice
from am_print_executor.verified_print_preparation import finalize_verified_geometry, prepare_verified_print


def box_mesh(extents, center):
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(center)
    return mesh


def report():
    return {"status": "blocked", "dangerous_layer_count": 1,
            "dangerous_layers": [{"z_mm": 6., "issues": [{"kind": "unsupported_extrusion_region",
            "area_mm2": .16, "xy_bounds_mm": [[5., -.2], [5.4, .2]], "features": ["overhang wall"]}]}]}


def write_project(path, mesh, offsets):
    vertices = "".join(f'<vertex x="{v[0]}" y="{v[1]}" z="{v[2]}"/>' for v in mesh.vertices)
    faces = "".join(f'<triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>' for f in mesh.faces)
    model = f'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources><object id="1" type="model"><mesh><vertices>{vertices}</vertices><triangles>{faces}</triangles></mesh></object></resources><build><item objectid="1"/></build></model>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr("Metadata/project_settings.config", json.dumps({"extruder_offset": offsets}))


class CoordinateContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mesh = box_mesh((20, 20, 20), (0, 0, 10))
        self.source = self.root / "source.stl"
        self.mesh.export(self.source)
        self.artifact = self.root / "slice.3mf"
        write_project(self.artifact, self.mesh, ["3x-7"])

    def test_request_maps_offset_from_artifact_without_changing_decisions(self):
        gate = report()
        gate["artifact"] = str(self.artifact)
        unchanged = copy.deepcopy(gate)
        request = build_printability_repair_request(source_geometry=self.source, gate_report=gate)
        self.assertEqual(request.gate_status, "blocked")
        self.assertEqual(request.coordinate_mapping["translation_xy_mm"], [3, -7])
        np.testing.assert_allclose(request.blocker_clusters[0].xy_bounds_mm, [[8, -7.2], [8.4, -6.8]])
        self.assertEqual(gate, unchanged)

    def test_mismatched_source_or_multiple_tool_frames_fail_closed(self):
        moved = self.mesh.copy()
        moved.apply_translation((0, 1, 0))
        with self.assertRaisesRegex(ValueError, "does_not_match"):
            verify_source_matches_slice(moved, self.artifact)
        write_project(self.artifact, self.mesh, ["0x0", "2x0"])
        with self.assertRaisesRegex(ValueError, "per_tool"):
            gate_report_in_geometry_frame(report(), self.artifact)

    def test_coordinate_mapping_cannot_be_applied_twice(self):
        mapped = gate_report_in_geometry_frame(report(), self.artifact)
        with self.assertRaisesRegex(ValueError, "already_mapped"):
            gate_report_in_geometry_frame(mapped, self.artifact)


class StructuralDesignTests(unittest.TestCase):
    def setUp(self):
        base = box_mesh((20, 20, 1), (0, 0, .5))
        upright = box_mesh((3, 4, 8), (0, 0, 4.5))
        arm = box_mesh((8, 3, 1), (3, 0, 6.5))
        self.mesh = trimesh.boolean.union([base, upright, arm], engine="manifold")
        self.clusters = cluster_gate_blockers(report())

    def test_rib_is_grounded_and_preserves_height_and_planar_base(self):
        candidate, action = add_integrated_support_ribs(self.mesh, self.clusters)
        self.assertTrue(candidate.is_volume)
        self.assertEqual(candidate.body_count, 1)
        np.testing.assert_allclose(candidate.bounds, self.mesh.bounds)
        self.assertGreater(candidate.volume, self.mesh.volume)
        self.assertTrue(inspect_flat_printing_base(candidate)["base_flatness_passed"])
        self.assertTrue(action["design_change"])
        self.assertFalse(action["fidelity_limited_gpr"])
        self.assertGreaterEqual(action["ribs"][0]["minimum_width_mm"], 1.2)

    def test_protected_hole_region_and_unknown_region_are_not_filled(self):
        for protected in ({"bounds_mm": [[4.7, -1, .1], [5.9, 1, 4]]}, {"name": "unlocated_hole"}):
            with self.assertRaises(ValueError):
                add_integrated_support_ribs(self.mesh, self.clusters, critical_regions=(protected,))
            with self.assertRaises(ValueError):
                add_printable_foundation(self.mesh, critical_regions=(protected,))

    def test_detached_island_is_rejected_before_slice_or_added_support(self):
        detached = trimesh.util.concatenate([self.mesh, box_mesh((1, 1, 1), (0, 0, 15))])
        with self.assertRaisesRegex(ValueError, "one_valid_connected"):
            add_integrated_support_ribs(detached, self.clusters)
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "floating.stl", Path(tmp) / "must_not_exist.3mf"
            detached.export(source)
            with patch("am_print_executor.verified_print_preparation.run_bambu_cli") as runner:
                with self.assertRaisesRegex(ValueError, "one_connected_solid"):
                    prepare_verified_print(source, output, allow_structural_changes=True)
            runner.assert_not_called()
            self.assertFalse(output.exists())


class FinalAcceptanceTests(unittest.TestCase):
    def test_blocked_pre_slice_never_runs_auto_orient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "cube.stl"
            mesh = box_mesh((10, 10, 10), (0, 0, 5))
            mesh.export(source)
            artifact = root / "prior.3mf"
            write_project(artifact, mesh, ["0x0"])
            with patch("am_print_executor.verified_print_preparation.inspect_final_gcode_printability", return_value=report()), \
                 patch("am_print_executor.verified_print_preparation.auto_orient_with_bambu_cli") as orient:
                with self.assertRaisesRegex(ValueError, "must_pass_before_auto_orient"):
                    finalize_verified_geometry(source=source, prior_artifact=artifact, directory=root / "final",
                        studio_exe=root / "studio", machine_json=root / "machine", process_json=root / "process", filament_jsons=[])
            orient.assert_not_called()

    def test_post_orientation_failure_is_not_promoted_to_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "cube.stl"
            mesh = box_mesh((10, 10, 10), (0, 0, 5))
            mesh.export(source)
            artifact = root / "prior.3mf"
            write_project(artifact, mesh, ["0x0"])
            final = {"artifact": artifact, "geometry": source, "gate": report(),
                     "flat_base": inspect_flat_printing_base(mesh)}
            with patch("am_print_executor.verified_print_preparation.inspect_final_gcode_printability", return_value={"status": "pass"}), \
                 patch("am_print_executor.verified_print_preparation.auto_orient_with_bambu_cli", return_value={"status": "auto_orient_complete"}), \
                 patch("am_print_executor.verified_print_preparation.slice_fixed_geometry", return_value=final):
                result = finalize_verified_geometry(source=source, prior_artifact=artifact, directory=root / "final",
                    studio_exe=root / "studio", machine_json=root / "machine", process_json=root / "process", filament_jsons=[])
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["physical_print_performed"])


if __name__ == "__main__":
    unittest.main()

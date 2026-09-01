from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

import trimesh

from am_print_executor.bambu_headless_cli import BambuCliResult
from am_print_executor.verified_print_preparation import (
    PIPELINE_NAME,
    prepare_verified_print,
    slice_fixed_geometry,
)


def grounded_box() -> trimesh.Trimesh:
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.apply_translation((0, 0, 5))
    return mesh


class BambuNativeTreePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.stl"
        grounded_box().export(self.source)
        self.studio = self.root / "bambu-studio.exe"
        self.studio.write_bytes(b"exe")
        self.machine = self.root / "machine.json"
        self.machine.write_text(json.dumps({
            "printable_area": ["0x0", "256x0", "256x256", "0x256"],
            "nozzle_diameter": ["0.4"],
        }))
        self.process = self.root / "process.json"
        self.process.write_text(json.dumps({
            "layer_height": "0.2",
            "outer_wall_line_width": "0.42",
        }))
        self.filament = self.root / "filament.json"
        self.filament.write_text("{}")

    def resolved_profiles(self):
        return {
            "machine": self.machine,
            "process": self.process,
            "filaments": [self.filament],
        }

    def sliced_result(self, directory: Path):
        directory.mkdir(parents=True)
        artifact = directory / "candidate.gcode.3mf"
        artifact.write_bytes(b"native-tree-gcode")
        return {
            "artifact": artifact,
            "support_type": "tree(auto)",
            "support_style": "tree_hybrid",
            "headless": True,
            "bambu_slice_succeeded": True,
            "post_slice_validation_performed": False,
        }

    def run_preparation(self):
        output = self.root / "output.gcode.3mf"
        def oriented_result(**kwargs):
            project = Path(kwargs["output_path"])
            project.write_bytes(b"auto-oriented-project")
            return {
                "status": "auto_orient_complete",
                "headless": True,
                "output": {"path": str(project)},
            }
        with patch(
            "am_print_executor.bambu_profile_resolver.materialize_bambu_cli_profiles",
            return_value=self.resolved_profiles(),
        ), patch(
            "am_print_executor.developer_mode_backend_v1120.discover_bambu_profiles",
            return_value={"machine": self.machine, "process": self.process, "filament": self.filament},
        ), patch(
            "am_print_executor.verified_print_preparation.auto_orient_with_bambu_cli",
            side_effect=oriented_result,
        ) as orient, patch(
            "am_print_executor.verified_print_preparation.slice_fixed_geometry",
            side_effect=lambda **kw: self.sliced_result(kw["directory"]),
        ):
            result = prepare_verified_print(
                self.source,
                output,
                studio_exe=self.studio,
                machine_json=self.machine,
                process_json=self.process,
                filament_jsons=[self.filament],
            )
        return output, result, orient

    def test_uses_only_native_tree_support_and_background_auto_orient(self):
        output, result, orient = self.run_preparation()
        self.assertTrue(output.is_file())
        self.assertEqual(result["status"], "slice_complete")
        self.assertEqual(result["pipeline"], PIPELINE_NAME)
        self.assertEqual(result["support_generator"], "bambu_studio_native")
        self.assertEqual(result["support_type"], "tree(auto)")
        self.assertEqual(result["support_style"], "tree_hybrid")
        self.assertTrue(result["headless"])
        self.assertFalse(result["model_self_support_required"])
        self.assertEqual(result["acceptance_basis"], "bambu_cli_slice_success")
        self.assertFalse(result["post_slice_validation_performed"])
        self.assertFalse(orient.call_args.kwargs["require_flat_source"])
        self.assertFalse(orient.call_args.kwargs["preserve_source_upright"])
        preparation = json.loads(
            (Path(result["preparation_directory"]) / "preparation.json").read_text()
        )
        self.assertFalse(preparation["structural_geometry_changes_applied"])
        profile = json.loads(
            (Path(result["preparation_directory"]) / "bambu_tree_support_process.json").read_text()
        )
        self.assertEqual(profile["enable_support"], "1")
        self.assertEqual(profile["support_type"], "tree(auto)")
        self.assertEqual(profile["support_style"], "tree_hybrid")

    def test_bambu_slice_success_is_published_without_post_slice_validation(self):
        output, result, orient = self.run_preparation()
        self.assertEqual(result["status"], "slice_complete")
        self.assertTrue(output.exists())
        self.assertEqual(orient.call_count, 1)
        self.assertIsNone(result["terminal_blocker"])
        receipt = result["acceptance"]
        self.assertEqual(receipt["acceptance_basis"], "bambu_cli_slice_success")
        self.assertFalse(receipt["post_slice_validation_performed"])
        self.assertNotIn("flat_base", receipt)
        self.assertNotIn("removal", receipt)
        self.assertNotIn("dangerous_layer_count", receipt)

    def test_disconnected_source_is_rejected_before_bambu(self):
        floating = trimesh.util.concatenate([
            grounded_box(),
            trimesh.creation.box((1, 1, 1)),
        ])
        floating.vertices[-8:] += (20, 0, 20)
        floating.export(self.source)
        with patch(
            "am_print_executor.verified_print_preparation.auto_orient_with_bambu_cli"
        ) as orient:
            with self.assertRaisesRegex(ValueError, "one_connected_solid"):
                prepare_verified_print(
                    self.source,
                    self.root / "no.gcode.3mf",
                    studio_exe=self.studio,
                    machine_json=self.machine,
                    process_json=self.process,
                    filament_jsons=[self.filament],
                )
        orient.assert_not_called()

    def test_permanent_custom_support_mode_is_removed(self):
        with self.assertRaisesRegex(ValueError, "unknown_support_mode"):
            prepare_verified_print(
                self.source,
                self.root / "no.gcode.3mf",
                studio_exe=self.studio,
                support_mode="permanent",
            )

    def test_slice_command_forces_tree_auto_through_hidden_runner(self):
        directory = self.root / "slice"
        execution = BambuCliResult(
            command=(), raw_exit=0, signed_exit=0, stdout="", stderr="",
            expected_outputs=(), outputs_exist=True, success=True,
            lock_wait_seconds=0.0, process_settle_seconds=0.0,
        )

        def run(command, **kwargs):
            (directory / "candidate.gcode.3mf").write_bytes(b"gcode")
            return execution

        with patch(
            "am_print_executor.verified_print_preparation.run_bambu_cli",
            side_effect=run,
        ) as runner:
            result = slice_fixed_geometry(
                source=self.source,
                directory=directory,
                studio_exe=self.studio,
                machine_json=self.machine,
                process_json=self.process,
                filament_jsons=[self.filament],
            )
        command = runner.call_args.args[0]
        self.assertIn("--support-type=tree(auto)", command)
        self.assertTrue(result["headless"])
        self.assertTrue(result["bambu_slice_succeeded"])
        self.assertFalse(result["post_slice_validation_performed"])


if __name__ == "__main__":
    unittest.main()

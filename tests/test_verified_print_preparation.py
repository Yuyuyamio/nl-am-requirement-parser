from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import trimesh

from am_print_executor.bambu_headless_cli import BambuCliResult
from am_print_executor.bambu_project_repair import (
    finalize_bambu_gcode_3mf,
    validate_bambu_gcode_3mf,
)
from am_print_executor.verified_print_preparation import (
    PIPELINE_NAME,
    prepare_verified_print,
    slice_fixed_geometry,
)


def grounded_box() -> trimesh.Trimesh:
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.apply_translation((0, 0, 5))
    return mesh


def textured_settings() -> dict:
    return {
        "curr_bed_type": "Textured PEI Plate",
        "filament_type": ["PLA"],
        "textured_plate_temp": ["55"],
        "textured_plate_temp_initial_layer": ["55"],
    }


def write_project(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/model_settings.config", "<config/>")
        archive.writestr("Metadata/slice_info.config", "<config/>")
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(textured_settings()),
        )


def write_gcode_project(path: Path, *, malformed_xml: bool = False) -> None:
    gcode = b"""; curr_bed_type = Textured PEI Plate
;curr_bed_type=Textured PEI Plate
; filament_type = PLA
; textured_plate_temp = 55
; textured_plate_temp_initial_layer = 55
M140 S55 ;set bed temp
M190 S55 ;wait for bed temp
G29.1 Z-0.04 ; for Textured PEI Plate
G1 X1 Y1 E1
"""
    model_settings = (
        '<config>\n<metadata key="compatible_printers" value=""X1";"P1""/>\n</config>'
        if malformed_xml
        else "<config/>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/model_settings.config", model_settings)
        archive.writestr("Metadata/slice_info.config", "<config/>")
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(textured_settings()),
        )
        archive.writestr(
            "Metadata/plate_1.json",
            json.dumps({"bed_type": "textured_plate"}),
        )
        archive.writestr("Metadata/plate_1.gcode", gcode)
        archive.writestr("Metadata/plate_1.gcode.md5", "STALE")


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
        self.filament.write_text(json.dumps({
            "filament_type": ["PLA"],
            "textured_plate_temp": ["35"],
            "textured_plate_temp_initial_layer": ["35"],
        }))

    def resolved_profiles(self):
        return {
            "machine": self.machine,
            "process": self.process,
            "filaments": [self.filament],
        }

    def sliced_result(self, directory: Path):
        directory.mkdir(parents=True)
        artifact = directory / "candidate.gcode.3mf"
        write_gcode_project(artifact, malformed_xml=True)
        finalization = finalize_bambu_gcode_3mf(artifact)
        return {
            "artifact": artifact,
            "support_type": "tree(auto)",
            "support_style": "tree_hybrid",
            "headless": True,
            "bambu_slice_succeeded": True,
            "build_plate": "Textured PEI Plate",
            "post_slice_validation_performed": True,
            "post_slice_validation": finalization,
        }

    def run_preparation(self):
        output = self.root / "output.gcode.3mf"
        def oriented_result(**kwargs):
            project = Path(kwargs["output_path"])
            write_project(project)
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
        self.assertEqual(
            result["acceptance_basis"],
            "bambu_cli_slice_and_artifact_validation",
        )
        self.assertTrue(result["post_slice_validation_performed"])
        self.assertEqual(result["build_plate"]["curr_bed_type"], "Textured PEI Plate")
        self.assertEqual(result["build_plate"]["bed_type"], "textured_plate")
        self.assertEqual(result["build_plate"]["pla_bed_temperature_c"], 55)
        self.assertEqual(result["build_plate"]["z_compensation_mm"], -0.04)
        self.assertFalse(orient.call_args.kwargs["require_flat_source"])
        self.assertFalse(orient.call_args.kwargs["preserve_source_upright"])
        self.assertTrue(orient.call_args.kwargs["trust_bambu_result"])
        self.assertEqual(orient.call_args.kwargs["build_plate"], "Textured PEI Plate")
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
        filament = json.loads(self.filament.read_text())
        self.assertEqual(filament["textured_plate_temp"], ["55"])
        self.assertEqual(filament["textured_plate_temp_initial_layer"], ["55"])
        self.assertEqual(
            validate_bambu_gcode_3mf(output)["status"],
            "bambu_gcode_3mf_validated",
        )

    def test_bambu_slice_success_is_published_only_after_post_slice_validation(self):
        output, result, orient = self.run_preparation()
        self.assertEqual(result["status"], "slice_complete")
        self.assertTrue(output.exists())
        self.assertEqual(orient.call_count, 1)
        self.assertIsNone(result["terminal_blocker"])
        receipt = result["acceptance"]
        self.assertEqual(
            receipt["acceptance_basis"],
            "bambu_cli_slice_and_artifact_validation",
        )
        self.assertTrue(receipt["post_slice_validation_performed"])
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
            write_gcode_project(
                directory / "candidate.gcode.3mf",
                malformed_xml=True,
            )
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
        self.assertEqual(
            command[command.index("--curr-bed-type") + 1],
            "Textured PEI Plate",
        )
        self.assertTrue(result["headless"])
        self.assertTrue(result["bambu_slice_succeeded"])
        self.assertTrue(result["post_slice_validation_performed"])
        self.assertTrue(
            result["post_slice_validation"]["xml_repair"]["repaired"]
        )
        self.assertTrue(
            result["post_slice_validation"]["gcode_md5"]["rewritten"]
        )
        with zipfile.ZipFile(directory / "candidate.gcode.3mf") as archive:
            gcode = archive.read("Metadata/plate_1.gcode")
            stored = archive.read("Metadata/plate_1.gcode.md5").decode("ascii")
        self.assertEqual(stored, hashlib.md5(gcode).hexdigest().upper())


if __name__ == "__main__":
    unittest.main()

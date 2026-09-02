from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from am_print_executor.bambu_project_repair import (
    finalize_bambu_gcode_3mf,
    validate_bambu_gcode_3mf,
    validate_bambu_project_3mf,
)


def settings() -> dict:
    return {
        "curr_bed_type": "Textured PEI Plate",
        "filament_type": ["PLA"],
        "textured_plate_temp": ["55"],
        "textured_plate_temp_initial_layer": ["55"],
    }


def write_project(path: Path, *, malformed: bool = False) -> None:
    model_settings = (
        '<config><metadata key="name" value="bad & value"/></config>'
        if malformed
        else "<config/>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr("Metadata/model_settings.config", model_settings)
        archive.writestr("Metadata/slice_info.config", "<config/>")
        archive.writestr("Metadata/project_settings.config", json.dumps(settings()))


def write_gcode_project(
    path: Path,
    *,
    bed_name: str = "Textured PEI Plate",
    bed_type: str = "textured_plate",
    malformed_xml: bool = True,
) -> bytes:
    project_settings = settings()
    project_settings["curr_bed_type"] = bed_name
    gcode = f"""; curr_bed_type = {bed_name}
M140 S55
M190 S55
G29.1 Z-0.04 ; for Textured PEI Plate
G1 X1 Y1 E1
""".encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("3D/3dmodel.model", "<model/>")
        archive.writestr(
            "Metadata/model_settings.config",
            (
                '<config>\n<metadata key="compatible_printers" value=""X1";"P1""/>\n</config>'
                if malformed_xml
                else "<config/>"
            ),
        )
        archive.writestr("Metadata/slice_info.config", "<config/>")
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(project_settings),
        )
        archive.writestr("Metadata/plate_1.json", json.dumps({"bed_type": bed_type}))
        archive.writestr("Metadata/plate_1.gcode", gcode)
        archive.writestr("Metadata/plate_1.gcode.md5", "STALE")
    return gcode


class BambuProjectFinalizationTests(unittest.TestCase):
    def test_finalization_repairs_xml_recalculates_md5_and_validates_plate(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "candidate.gcode.3mf"
            gcode = write_gcode_project(artifact)

            result = finalize_bambu_gcode_3mf(artifact)

            self.assertEqual(result["status"], "bambu_gcode_3mf_finalized")
            self.assertTrue(result["xml_repair"]["repaired"])
            self.assertTrue(result["gcode_md5"]["rewritten"])
            self.assertEqual(
                result["validation"]["build_plate"]["bed_type"],
                "textured_plate",
            )
            self.assertEqual(
                result["validation"]["build_plate"]["z_compensation_mm"],
                -0.04,
            )
            with zipfile.ZipFile(artifact) as archive:
                stored = archive.read("Metadata/plate_1.gcode.md5").decode()
            self.assertEqual(stored, hashlib.md5(gcode).hexdigest().upper())

    def test_read_only_validation_rejects_cool_plate_even_with_valid_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "cool.gcode.3mf"
            write_gcode_project(
                artifact,
                bed_name="Cool Plate",
                bed_type="cool_plate",
                malformed_xml=False,
            )
            with self.assertRaisesRegex(RuntimeError, "build plate mismatch"):
                validate_bambu_gcode_3mf(artifact)

    def test_project_validation_rejects_hashable_but_invalid_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "editable.3mf"
            write_project(project, malformed=True)
            self.assertEqual(len(hashlib.sha256(project.read_bytes()).hexdigest()), 64)
            with self.assertRaisesRegex(RuntimeError, "invalid XML"):
                validate_bambu_project_3mf(project)


if __name__ == "__main__":
    unittest.main()

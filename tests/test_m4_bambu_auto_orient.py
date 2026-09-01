from __future__ import annotations

import tempfile
import unittest
import zipfile

import trimesh

from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from xml.etree import ElementTree as ET

import am_print_executor.bambu_auto_orient as orient


def _result(
    command,
    *,
    exit_code=0,
    output_exists=True,
):
    return SimpleNamespace(
        command=tuple(command),
        raw_exit=exit_code,
        signed_exit=exit_code,
        stdout="",
        stderr="native failure" if exit_code else "",
        outputs_exist=output_exists,
        success=(exit_code == 0 and output_exists),
        lock_wait_seconds=0.0,
        process_settle_seconds=0.0,
    )


def _write_project(
    path: Path,
    *,
    malformed_bambu_value: bool = False,
    build_transform: str = "1 0 0 0 1 0 0 0 1 0 0 0",
):
    value = (
        'value=""X1C";"P1S""'
        if malformed_bambu_value
        else 'value="ok"'
    )
    model_settings = f"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="1">
    <metadata key="compatible_printers" {value}/>
    <part id="1" subtype="normal_part">
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
    </part>
  </object>
  <plate><model_instance/></plate>
</config>
"""
    mesh = trimesh.creation.box(extents=(20, 20, 20))
    mesh.apply_translation((0, 0, 10))
    vertices = "".join(f'<vertex x="{v[0]}" y="{v[1]}" z="{v[2]}"/>' for v in mesh.vertices)
    triangles = "".join(f'<triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>' for f in mesh.faces)
    model = f"""<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources><object id="1" type="model"><mesh><vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object></resources>
  <build><item objectid="1" transform="{build_transform}"/></build>
</model>
"""

    path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr(
            "Metadata/model_settings.config",
            model_settings,
        )
        archive.writestr(
            "Metadata/project_settings.config",
            "{}",
        )
        archive.writestr(
            "Metadata/slice_info.config",
            "<config/>",
        )


class BambuAutoOrientTests(unittest.TestCase):
    def test_valid_xml_with_tilted_world_geometry_is_rejected(self):
        # A finite transform and a valid closed mesh are insufficient when
        # the transformed bottom is an edge rather than a planar footprint.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tilted.3mf"
            _write_project(path, build_transform="1 0 0 0 0.70710678 0.70710678 0 -0.70710678 0.70710678 0 0 7.0710678")
            with self.assertRaisesRegex(orient.BambuAutoOrientError, "stable planar bed contact"):
                orient.inspect_auto_oriented_project(path)

    def test_valid_xml_with_model_above_plate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "elevated.3mf"
            _write_project(path, build_transform="1 0 0 0 1 0 0 0 1 0 0 3")
            with self.assertRaisesRegex(orient.BambuAutoOrientError, "stable planar bed contact"):
                orient.inspect_auto_oriented_project(path)

    def _inputs(self, root: Path):
        studio = root / "bambu-studio.exe"
        source = root / "model.stl"
        machine = root / "machine.json"
        process = root / "process.json"
        filament = root / "filament.json"

        for path in (studio, machine, process, filament):
            path.write_bytes(b"x")
        box = trimesh.creation.box(extents=(20.0, 20.0, 20.0))
        box.apply_translation((0.0, 0.0, 10.0))
        box.export(source)

        return studio, source, machine, process, filament

    def test_transient_native_failure_retries_fresh_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, source, machine, process, filament = self._inputs(root)
            output = root / "oriented.project.3mf"
            calls = []

            def fake_run(command, **kwargs):
                calls.append(list(command))

                if len(calls) == 1:
                    return _result(
                        command,
                        exit_code=-6,
                        output_exists=False,
                    )

                candidate = Path(
                    command[
                        command.index("--export-3mf") + 1
                    ]
                )
                _write_project(candidate)
                return _result(command)

            with mock.patch.object(
                orient,
                "run_bambu_cli",
                side_effect=fake_run,
            ):
                result = orient.auto_orient_with_bambu_cli(
                    studio_exe=studio,
                    source_model=source,
                    output_path=output,
                    machine_json=machine,
                    process_json=process,
                    filament_jsons=[filament],
                    max_attempts=3,
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(result["attempt_count"], 2)
            self.assertTrue(output.is_file())
            self.assertNotIn("--slice", calls[1])
            self.assertEqual(
                calls[1][calls[1].index("--orient") + 1],
                "1",
            )

    def test_bambu_malformed_xml_is_repaired_before_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, source, machine, process, filament = self._inputs(root)
            output = root / "oriented.project.3mf"

            def fake_run(command, **kwargs):
                candidate = Path(
                    command[
                        command.index("--export-3mf") + 1
                    ]
                )
                _write_project(
                    candidate,
                    malformed_bambu_value=True,
                )
                return _result(command)

            with mock.patch.object(
                orient,
                "run_bambu_cli",
                side_effect=fake_run,
            ):
                result = orient.auto_orient_with_bambu_cli(
                    studio_exe=studio,
                    source_model=source,
                    output_path=output,
                    machine_json=machine,
                    process_json=process,
                    filament_jsons=[filament],
                )

            self.assertTrue(
                result["output"]["xml_repair"]["repaired"]
            )

            with zipfile.ZipFile(output) as archive:
                ET.fromstring(
                    archive.read(
                        "Metadata/model_settings.config"
                    )
                )

    def test_stale_destination_is_never_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, source, machine, process, filament = self._inputs(root)
            output = root / "oriented.project.3mf"
            output.write_bytes(b"stale")

            def fake_run(command, **kwargs):
                return _result(
                    command,
                    exit_code=-6,
                    output_exists=False,
                )

            with mock.patch.object(
                orient,
                "run_bambu_cli",
                side_effect=fake_run,
            ):
                with self.assertRaises(
                    orient.BambuAutoOrientError
                ):
                    orient.auto_orient_with_bambu_cli(
                        studio_exe=studio,
                        source_model=source,
                        output_path=output,
                        machine_json=machine,
                        process_json=process,
                        filament_jsons=[filament],
                        max_attempts=2,
                    )

            self.assertEqual(output.read_bytes(), b"stale")

    def test_curved_base_is_blocked_before_bambu_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            studio, source, machine, process, filament = self._inputs(root)
            sphere = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
            sphere.apply_translation((0.0, 0.0, 10.0))
            sphere.export(source)
            output = root / "oriented.project.3mf"

            with mock.patch.object(orient, "run_bambu_cli") as run:
                with self.assertRaisesRegex(
                    orient.BambuAutoOrientError,
                    "FLAT_BASE_GATE = BLOCK",
                ):
                    orient.auto_orient_with_bambu_cli(
                        studio_exe=studio,
                        source_model=source,
                        output_path=output,
                        machine_json=machine,
                        process_json=process,
                        filament_jsons=[filament],
                        max_attempts=1,
                    )

            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

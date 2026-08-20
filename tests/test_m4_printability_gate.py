import json
import tempfile
import unittest
import zipfile

from pathlib import Path

import trimesh

import am_print_executor.printability_gate as g


class PrintabilityGateTests(
    unittest.TestCase
):

    def write_artifact(
        self,
        root: Path,
        *,
        support: bool,
    ) -> Path:

        path = (
            root
            / "job.gcode.3mf"
        )

        settings = {
            "curr_bed_type":
                "Textured PEI Plate",
            "enable_support":
                "1",
            "support_type":
                "tree(auto)",
            "detect_floating_vertical_shell":
                "1",
            "detect_overhang_wall":
                "1",
            "bridge_no_support":
                "0",
        }

        feature = (
            "; FEATURE: Support\n"
            if support
            else "; FEATURE: Outer wall\n"
        )

        with zipfile.ZipFile(
            path,
            "w",
        ) as zf:

            zf.writestr(
                "Metadata/"
                "project_settings.config",
                json.dumps(settings),
            )

            zf.writestr(
                "Metadata/"
                "plate_1.gcode",
                feature
                + "G1 X0 Y0 Z0.2\n",
            )

            zf.writestr(
                "Metadata/"
                "slice_info.config",
                "<config/>",
            )

        return path


    def write_supported_geometry(
        self,
        root: Path,
    ) -> Path:

        base = trimesh.creation.box(
            extents=(20, 20, 2)
        )

        base.apply_translation(
            (0, 0, 1)
        )

        upper = trimesh.creation.box(
            extents=(10, 10, 2)
        )

        upper.apply_translation(
            (0, 0, 11)
        )

        mesh = trimesh.util.concatenate(
            [base, upper]
        )

        path = root / "geometry.stl"

        mesh.export(path)

        return path


    def test_support_disabled_is_blocker(
        self,
    ):
        result = (
            g.validate_support_settings(
                {
                    "enable_support":
                        "0",
                    "support_type":
                        "tree(auto)",
                    "detect_floating_vertical_shell":
                        "1",
                    "detect_overhang_wall":
                        "1",
                    "bridge_no_support":
                        "0",
                }
            )
        )

        self.assertIn(
            "automatic_support_is_disabled",
            result["blockers"],
        )


    def test_geometry_not_on_bed_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            mesh = trimesh.creation.box(
                extents=(10, 10, 10)
            )

            mesh.apply_translation(
                (0, 0, 15)
            )

            path = root / "floating.stl"
            mesh.export(path)

            result = g.inspect_geometry(
                path
            )

            self.assertIn(
                "geometry_is_not_on_build_plate",
                result["blockers"],
            )



    def test_geometry_on_bed_does_not_block(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            mesh = trimesh.creation.box(
                extents=[
                    10.0,
                    10.0,
                    10.0,
                ]
            )

            # trimesh boxes are centred on the origin.
            # Move it up by half its height so min Z == 0.
            mesh.apply_translation(
                [
                    0.0,
                    0.0,
                    5.0,
                ]
            )

            path = root / "on_bed.stl"
            mesh.export(path)

            result = g.inspect_geometry(
                path
            )

            self.assertNotIn(
                "geometry_is_not_on_build_plate",
                result["blockers"],
            )

    def test_overhang_without_support_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            result = g.build_report(
                artifact=
                    self.write_artifact(
                        root,
                        support=False,
                    ),
                geometry_project=
                    self.write_supported_geometry(
                        root
                    ),
            )

            self.assertEqual(
                result["status"],
                "printability_gate_blocked",
            )

            self.assertIn(
                "overhang_requires_support_but_"
                "no_support_or_bridge_toolpath_exists",
                result["blockers"],
            )


    def test_overhang_with_support_passes(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            result = g.build_report(
                artifact=
                    self.write_artifact(
                        root,
                        support=True,
                    ),
                geometry_project=
                    self.write_supported_geometry(
                        root
                    ),
            )

            self.assertEqual(
                result["status"],
                "printability_gate_pass",
            )


if __name__ == "__main__":
    unittest.main()

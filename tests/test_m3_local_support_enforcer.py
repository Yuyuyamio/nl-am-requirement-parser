import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import trimesh

from am_print_executor.local_support_enforcer import (
    write_support_enforcer_project,
)


class LocalSupportEnforcerTests(
    unittest.TestCase
):

    def test_project_contains_native_enforcer_volume(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)

            stl = d / "model.stl"

            trimesh.creation.box(
                extents=[
                    10.0,
                    10.0,
                    10.0,
                ]
            ).export(stl)

            tracks = {
                "policy": {
                    "measured_layer_height_mm":
                        0.2,

                    "max_support_vertical_gap_mm":
                        0.5,
                },

                "tracks": [
                    {
                        "z_start":
                            5.0,

                        "z_end":
                            6.0,

                        "bbox_mm": [
                            -2.0,
                            -2.0,
                            2.0,
                            2.0,
                        ],

                        "max_single_layer_area_mm2":
                            4.0,

                        "class_counts": {
                            "SUPPORT_TOO_FAR":
                                3,
                        },
                    }
                ],
            }

            report = d / "tracks.json"

            report.write_text(
                json.dumps(tracks),
                encoding="utf-8",
            )

            out = d / "project.3mf"

            result = (
                write_support_enforcer_project(
                    model_path=stl,
                    defect_tracks_path=report,
                    output_path=out,
                )
            )

            self.assertEqual(
                result["enforcer_count"],
                1,
            )

            with zipfile.ZipFile(
                out,
                "r",
            ) as archive:
                names = set(
                    archive.namelist()
                )

                self.assertIn(
                    "3D/3dmodel.model",
                    names,
                )

                self.assertIn(
                    "Metadata/Slic3r_PE_model.config",
                    names,
                )

                model_xml = archive.read(
                    "3D/3dmodel.model"
                )

                config_xml = archive.read(
                    "Metadata/Slic3r_PE_model.config"
                )

                ElementTree.fromstring(
                    model_xml
                )

                ElementTree.fromstring(
                    config_xml
                )

                text = config_xml.decode(
                    "utf-8"
                )

                self.assertIn(
                    'value="normal_part"',
                    text,
                )

                self.assertIn(
                    'value="support_enforcer"',
                    text,
                )


if __name__ == "__main__":
    unittest.main()

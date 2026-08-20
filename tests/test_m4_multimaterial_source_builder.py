import json
import tempfile
import unittest

from pathlib import Path

import trimesh

from am_print_executor.multimaterial_source_builder import (
    MultiMaterialSourceError,
    build_multimaterial_source,
)


class MultiMaterialSourceBuilderTests(
    unittest.TestCase
):

    def make_source(
        self,
        root: Path,
    ) -> Path:
        path = root / "source.stl"

        mesh = trimesh.creation.box(
            extents=[
                20.0,
                20.0,
                20.0,
            ]
        )

        mesh.apply_translation(
            [10.0, 10.0, 10.0]
        )

        mesh.export(path)

        return path


    def make_profiles(
        self,
        root: Path,
        count: int,
    ) -> list[Path]:
        result = []

        for index in range(
            1,
            count + 1,
        ):
            path = (
                root
                / f"filament_{index}.json"
            )

            path.write_text(
                json.dumps(
                    {
                        "name":
                            f"Test {index}"
                    }
                ),
                encoding="utf-8",
            )

            result.append(path)

        return result


    def test_two_material_ratio_partition(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            source = self.make_source(
                root
            )

            profiles = self.make_profiles(
                root,
                2,
            )

            out = root / "out"

            result = (
                build_multimaterial_source(
                    input_model=source,
                    output_dir=out,
                    job_request_id="TEST-2MAT",
                    axis="z",
                    cut_ratios=[0.5],
                    cut_positions_mm=None,
                    filament_profiles=
                        profiles,
                    position_mm=[
                        100.0,
                        110.0,
                        0.0,
                    ],
                )
            )

            self.assertEqual(
                result["region_count"],
                2,
            )

            manifest = json.loads(
                (
                    out
                    / "multimaterial_job.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                manifest["status"],
                "multimaterial_job_ready",
            )

            self.assertEqual(
                len(
                    manifest["objects"]
                ),
                2,
            )

            self.assertEqual(
                [
                    row[
                        "project_filament_ids"
                    ][0]
                    for row
                    in manifest["objects"]
                ],
                [1, 2],
            )

            self.assertTrue(
                manifest[
                    "partition"
                ][
                    "volume_conserved"
                ]
            )

            self.assertFalse(
                manifest["policy"][
                    "ams_slot_mapping_embedded"
                ]
            )


    def test_three_material_two_cut_partition(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            source = self.make_source(
                root
            )

            profiles = self.make_profiles(
                root,
                3,
            )

            out = root / "out"

            result = (
                build_multimaterial_source(
                    input_model=source,
                    output_dir=out,
                    job_request_id="TEST-3MAT",
                    axis="z",
                    cut_ratios=[
                        0.25,
                        0.75,
                    ],
                    cut_positions_mm=None,
                    filament_profiles=
                        profiles,
                    position_mm=[
                        120.0,
                        120.0,
                        0.0,
                    ],
                )
            )

            self.assertEqual(
                result["region_count"],
                3,
            )

            manifest = json.loads(
                (
                    out
                    / "multimaterial_job.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                manifest[
                    "partition"
                ][
                    "region_count"
                ],
                3,
            )

            self.assertEqual(
                len(
                    manifest[
                        "filament_profiles"
                    ]
                ),
                3,
            )

            for row in manifest[
                "generated_regions"
            ]:
                self.assertTrue(
                    row["is_watertight"]
                )

                self.assertTrue(
                    row["is_volume"]
                )


    def test_filament_count_mismatch_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            source = self.make_source(
                root
            )

            profiles = self.make_profiles(
                root,
                2,
            )

            with self.assertRaises(
                MultiMaterialSourceError
            ):
                build_multimaterial_source(
                    input_model=source,
                    output_dir=
                        root / "out",
                    job_request_id="TEST-BLOCK",
                    axis="z",
                    cut_ratios=[
                        0.25,
                        0.75,
                    ],
                    cut_positions_mm=None,
                    filament_profiles=
                        profiles,
                    position_mm=[
                        0.0,
                        0.0,
                        0.0,
                    ],
                )


if __name__ == "__main__":
    unittest.main()

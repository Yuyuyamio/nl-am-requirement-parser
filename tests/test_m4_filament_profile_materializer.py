import hashlib
import json
import tempfile
import unittest

from pathlib import Path

from am_print_executor.filament_profile_materializer import (
    FilamentMaterializerError,
    materialize_filament_profiles,
)


class FilamentProfileMaterializerTests(
    unittest.TestCase
):

    def make_profile(
        self,
        root: Path,
        *,
        list_colour: bool,
    ) -> Path:
        path = root / "base.json"

        colour = (
            ["#00AE42"]
            if list_colour
            else "#00AE42"
        )

        settings_id = (
            ["Base PLA"]
            if list_colour
            else "Base PLA"
        )

        path.write_text(
            json.dumps(
                {
                    "name": "Base PLA",
                    "type": "filament",
                    "from": "User",
                    "filament_colour":
                        colour,
                    "filament_settings_id":
                        settings_id,
                    "filament_type":
                        ["PLA"],
                    "filament_vendor":
                        ["Bambu Lab"],
                }
            ),
            encoding="utf-8",
        )

        return path


    def sha256(
        self,
        path: Path,
    ) -> str:
        return hashlib.sha256(
            path.read_bytes()
        ).hexdigest()


    def test_string_colour_shape(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            base = self.make_profile(
                root,
                list_colour=False,
            )

            before = self.sha256(base)

            result = (
                materialize_filament_profiles(
                    base_profiles=[base],
                    colours=[
                        "#A6A9AA",
                        "#F4EE2A",
                    ],
                    names=[
                        "Gray PLA",
                        "Yellow PLA",
                    ],
                    output_dir=
                        root / "out",
                )
            )

            self.assertEqual(
                result[
                    "logical_filament_count"
                ],
                2,
            )

            first = json.loads(
                (
                    root
                    / "out"
                    / "filament_01.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                first[
                    "filament_colour"
                ],
                "#A6A9AA",
            )

            self.assertEqual(
                before,
                self.sha256(base),
            )


    def test_list_colour_shape_and_three_materials(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            base = self.make_profile(
                root,
                list_colour=True,
            )

            result = (
                materialize_filament_profiles(
                    base_profiles=[base],
                    colours=[
                        "#111111",
                        "#222222",
                        "#333333",
                    ],
                    output_dir=
                        root / "out",
                )
            )

            self.assertEqual(
                result[
                    "logical_filament_count"
                ],
                3,
            )

            third = json.loads(
                (
                    root
                    / "out"
                    / "filament_03.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                third[
                    "filament_colour"
                ],
                ["#333333"],
            )


    def test_real_resolved_bambu_profile_may_omit_colour(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            base = root / "base.json"

            base.write_text(
                json.dumps(
                    {
                        "name":
                            "NL-AM CLI Full - Bambu PLA Basic @BBL X1C",

                        "type":
                            "filament",

                        "from":
                            "User",

                        "inherits":
                            "Bambu PLA Basic @BBL X1C",

                        "filament_type":
                            ["PLA"],

                        "filament_vendor":
                            ["Bambu Lab"],

                        "filament_settings_id": [
                            "NL-AM CLI Full - Bambu PLA Basic @BBL X1C"
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = (
                materialize_filament_profiles(
                    base_profiles=[base],
                    colours=[
                        "#A6A9AA",
                        "#F4EE2A",
                    ],
                    output_dir=
                        root / "out",
                )
            )

            self.assertEqual(
                result[
                    "logical_filament_count"
                ],
                2,
            )

            first = json.loads(
                (
                    root
                    / "out"
                    / "filament_01.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            second = json.loads(
                (
                    root
                    / "out"
                    / "filament_02.json"
                ).read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                first["filament_colour"],
                ["#A6A9AA"],
            )

            self.assertEqual(
                second["filament_colour"],
                ["#F4EE2A"],
            )

            self.assertNotIn(
                "ams_mapping",
                first,
            )

            self.assertNotIn(
                "ams_mapping",
                second,
            )



    def test_invalid_colour_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            base = self.make_profile(
                root,
                list_colour=False,
            )

            with self.assertRaises(
                FilamentMaterializerError
            ):
                materialize_filament_profiles(
                    base_profiles=[base],
                    colours=["yellow"],
                    output_dir=
                        root / "out",
                )


    def test_non_filament_profile_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            base = root / "machine.json"

            base.write_text(
                json.dumps(
                    {
                        "name":
                            "X1C Machine",

                        "type":
                            "machine",

                        "filament_type":
                            ["PLA"],

                        "filament_colour":
                            ["#FFFFFF"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(
                FilamentMaterializerError
            ):
                materialize_filament_profiles(
                    base_profiles=[base],
                    colours=["#FFFFFF"],
                    output_dir=
                        root / "out",
                )



    def test_device_mapping_field_blocks(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            base = self.make_profile(
                root,
                list_colour=False,
            )

            data = json.loads(
                base.read_text(
                    encoding="utf-8"
                )
            )

            data["ams_mapping"] = [
                0,
                3,
            ]

            base.write_text(
                json.dumps(data),
                encoding="utf-8",
            )

            with self.assertRaises(
                FilamentMaterializerError
            ):
                materialize_filament_profiles(
                    base_profiles=[base],
                    colours=["#FFFFFF"],
                    output_dir=
                        root / "out",
                )


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
import zipfile

from pathlib import Path

from am_print_executor.multimaterial_path_outside_resolver import (
    classify_prime_tower_ab,
    clone_project_with_setting_overrides,
)


class PathOutsideResolverTests(
    unittest.TestCase
):

    def make_project(
        self,
        root: Path,
    ) -> Path:

        path = root / "source.3mf"

        settings = {
            "enable_prime_tower": "1",
            "wipe_tower_x": ["165"],
            "wipe_tower_y": ["250"],
            "prime_tower_width": "35",
            "prime_tower_brim_width": "3",
        }

        with zipfile.ZipFile(
            path,
            "w",
        ) as zf:

            zf.writestr(
                "Metadata/project_settings.config",
                json.dumps(settings),
            )

            zf.writestr(
                "dummy.txt",
                "unchanged",
            )

        return path


    def test_clone_does_not_modify_source(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            source = self.make_project(root)

            before = source.read_bytes()

            variant = root / "variant.3mf"

            result = (
                clone_project_with_setting_overrides(
                    source=source,
                    destination=variant,
                    overrides={
                        "enable_prime_tower": "0",
                    },
                )
            )

            self.assertEqual(
                source.read_bytes(),
                before,
            )

            self.assertFalse(
                result["source_modified"]
            )


    def test_variant_changes_only_requested_setting(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            source = self.make_project(root)

            variant = root / "variant.3mf"

            clone_project_with_setting_overrides(
                source=source,
                destination=variant,
                overrides={
                    "enable_prime_tower": "0",
                },
            )

            with zipfile.ZipFile(
                variant,
                "r",
            ) as zf:

                settings = json.loads(
                    zf.read(
                        "Metadata/project_settings.config"
                    ).decode("utf-8")
                )

                self.assertEqual(
                    zf.read("dummy.txt"),
                    b"unchanged",
                )

            self.assertEqual(
                settings["enable_prime_tower"],
                "0",
            )

            self.assertEqual(
                settings["wipe_tower_x"],
                ["165"],
            )

            self.assertEqual(
                settings["wipe_tower_y"],
                ["250"],
            )


    def test_prime_tower_implicated_when_variant_passes(
        self,
    ):
        result = classify_prime_tower_ab(
            {
                "exit": {
                    "signed": 0,
                    "symbol": "CLI_SUCCESS",
                },
                "direct_slice_succeeded":
                    True,
            }
        )

        self.assertEqual(
            result["diagnosis"],
            "prime_tower_path_implicated",
        )


    def test_prime_tower_not_sufficient_on_minus_104(
        self,
    ):
        result = classify_prime_tower_ab(
            {
                "exit": {
                    "signed": -104,
                    "symbol":
                        "CLI_GCODE_PATH_OUTSIDE",
                },
                "direct_slice_succeeded":
                    False,
            }
        )

        self.assertEqual(
            result["diagnosis"],
            "prime_tower_not_sufficient",
        )


if __name__ == "__main__":
    unittest.main()

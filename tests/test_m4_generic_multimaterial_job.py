import json
import tempfile
import unittest
from pathlib import Path

from am_print_executor.multimaterial_job import (
    MultiMaterialJobError,
    load_multimaterial_job,
)

from am_print_executor.multimaterial_project import (
    build_assemble_payload,
)


class GenericMultiMaterialJobTests(
    unittest.TestCase
):

    def make_file(
        self,
        path: Path,
        text: str = "x",
    ):
        path.write_text(
            text,
            encoding="utf-8",
        )

        return path


    def test_three_filaments_four_objects(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            filaments = []

            for index in range(1, 4):
                path = self.make_file(
                    root
                    / f"f{index}.json",
                    "{}",
                )

                filaments.append(
                    {
                        "id": index,
                        "path": str(path),
                    }
                )

            models = []

            for index in range(4):
                models.append(
                    self.make_file(
                        root
                        / f"m{index}.stl",
                    )
                )

            manifest = root / "job.json"

            manifest.write_text(
                json.dumps(
                    {
                        "status":
                            "multimaterial_job_ready",

                        "filament_profiles":
                            filaments,

                        "objects": [
                            {
                                "path":
                                    str(models[0]),
                                "project_filament_ids":
                                    [1],
                            },
                            {
                                "path":
                                    str(models[1]),
                                "project_filament_ids":
                                    [2],
                            },
                            {
                                "path":
                                    str(models[2]),
                                "project_filament_ids":
                                    [3],
                            },
                            {
                                "path":
                                    str(models[3]),
                                "project_filament_ids":
                                    [1],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            job = load_multimaterial_job(
                manifest
            )

            self.assertEqual(
                job[
                    "project_filament_count"
                ],
                3,
            )

            self.assertEqual(
                len(job["objects"]),
                4,
            )

            payload = (
                build_assemble_payload(
                    manifest
                )
            )

            rows = (
                payload[
                    "plates"
                ][0][
                    "objects"
                ]
            )

            self.assertEqual(
                len(rows),
                4,
            )

            self.assertEqual(
                rows[0]["filaments"],
                [1],
            )

            self.assertEqual(
                rows[1]["filaments"],
                [2],
            )

            self.assertEqual(
                rows[2]["filaments"],
                [3],
            )

            self.assertEqual(
                rows[3]["filaments"],
                [1],
            )


    def test_non_contiguous_filament_ids_blocked(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            f1 = self.make_file(
                root / "f1.json",
                "{}",
            )

            f3 = self.make_file(
                root / "f3.json",
                "{}",
            )

            model = self.make_file(
                root / "m.stl"
            )

            manifest = root / "job.json"

            manifest.write_text(
                json.dumps(
                    {
                        "status":
                            "multimaterial_job_ready",

                        "filament_profiles": [
                            {
                                "id": 1,
                                "path": str(f1),
                            },
                            {
                                "id": 3,
                                "path": str(f3),
                            },
                        ],

                        "objects": [
                            {
                                "path":
                                    str(model),

                                "project_filament_ids":
                                    [1],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(
                MultiMaterialJobError
            ):
                load_multimaterial_job(
                    manifest
                )


    def test_undefined_filament_reference_blocked(
        self,
    ):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            filament = self.make_file(
                root / "f1.json",
                "{}",
            )

            model = self.make_file(
                root / "m.stl"
            )

            manifest = root / "job.json"

            manifest.write_text(
                json.dumps(
                    {
                        "status":
                            "multimaterial_job_ready",

                        "filament_profiles": [
                            {
                                "id": 1,
                                "path":
                                    str(filament),
                            }
                        ],

                        "objects": [
                            {
                                "path":
                                    str(model),

                                "project_filament_ids":
                                    [2],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(
                MultiMaterialJobError
            ):
                load_multimaterial_job(
                    manifest
                )


if __name__ == "__main__":
    unittest.main()

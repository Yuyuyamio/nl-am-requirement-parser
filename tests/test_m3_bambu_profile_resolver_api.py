import tempfile
import unittest
from pathlib import Path

from am_print_executor.bambu_profile_resolver import (
    materialize_bambu_cli_profiles,
)


class BambuProfileResolverApiTests(unittest.TestCase):

    def test_public_materialize_api_accepts_external_full_profiles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            studio = root / "Bambu Studio" / "bambu-studio.exe"
            studio.parent.mkdir(parents=True)
            studio.write_bytes(b"")

            machine = root / "machine.json"
            process = root / "process.json"
            filament = root / "filament.json"

            machine.write_text("{}", encoding="utf-8")
            process.write_text("{}", encoding="utf-8")
            filament.write_text("{}", encoding="utf-8")

            cache = root / "cache"

            result = materialize_bambu_cli_profiles(
                studio_exe=studio,
                machine_json=machine,
                process_json=process,
                filament_jsons=[filament],
                cache_dir=cache,
            )

            self.assertEqual(
                result["machine"],
                machine.resolve(),
            )

            self.assertEqual(
                result["process"],
                process.resolve(),
            )

            self.assertEqual(
                result["filaments"],
                [filament.resolve()],
            )


    def test_empty_filament_list_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            studio = root / "Bambu Studio" / "bambu-studio.exe"
            studio.parent.mkdir(parents=True)
            studio.write_bytes(b"")

            machine = root / "machine.json"
            process = root / "process.json"

            machine.write_text("{}", encoding="utf-8")
            process.write_text("{}", encoding="utf-8")

            with self.assertRaises(Exception):
                materialize_bambu_cli_profiles(
                    studio_exe=studio,
                    machine_json=machine,
                    process_json=process,
                    filament_jsons=[],
                    cache_dir=root / "cache",
                )


if __name__ == "__main__":
    unittest.main()

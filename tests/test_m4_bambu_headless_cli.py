import os
import subprocess
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import am_print_executor.bambu_headless_cli as h


@unittest.skipUnless(
    os.name == "nt",
    "Windows-only headless process policy",
)
class BambuHeadlessCliTests(
    unittest.TestCase
):

    def test_unsigned_exit_conversion(
        self,
    ):
        self.assertEqual(
            h.normalise_windows_exit(
                4294967290
            ),
            -6,
        )

        self.assertEqual(
            h.normalise_windows_exit(0),
            0,
        )


    def test_hidden_process_policy(
        self,
    ):
        fake = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout="CLI_OK",
            stderr="",
        )

        executable = Path(
            r"C:\Program Files\Bambu Studio"
            r"\bambu-studio.exe"
        )

        with patch.object(
            Path,
            "is_file",
            return_value=True,
        ), patch.object(
            h.subprocess,
            "run",
            return_value=fake,
        ) as mocked, patch.object(
            h,
            "_windows_bambu_process_ids",
            return_value=set(),
        ):

            result = h.run_bambu_cli(
                [
                    str(executable),
                    "--help",
                ]
            )

        self.assertTrue(
            result.success
        )

        self.assertGreaterEqual(
            result.process_settle_seconds,
            0.0,
        )

        kwargs = (
            mocked.call_args.kwargs
        )

        self.assertFalse(
            kwargs["shell"]
        )

        self.assertEqual(
            kwargs["stdin"],
            subprocess.DEVNULL,
        )

        self.assertTrue(
            kwargs["creationflags"]
            & subprocess.CREATE_NO_WINDOW
        )

        startup = kwargs[
            "startupinfo"
        ]

        self.assertTrue(
            startup.dwFlags
            & subprocess.STARTF_USESHOWWINDOW
        )

        self.assertEqual(
            startup.wShowWindow,
            subprocess.SW_HIDE,
        )


    def test_exit_zero_without_output_fails(
        self,
    ):
        fake = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout="",
            stderr="",
        )

        with tempfile.TemporaryDirectory() as tmp:
            executable = (
                Path(tmp)
                / "bambu-studio.exe"
            )
            executable.write_bytes(b"fake")
            missing = Path(tmp) / "missing.3mf"

            with patch.object(
                h.subprocess,
                "run",
                return_value=fake,
            ), patch.object(
                h,
                "_windows_bambu_process_ids",
                return_value=set(),
            ):
                result = h.run_bambu_cli(
                    [
                        str(executable),
                        "--slice",
                        "0",
                    ],
                    expected_outputs=[missing],
                )

        self.assertFalse(
            result.success
        )

        self.assertFalse(
            result.outputs_exist
        )


    def test_existing_bambu_process_blocks_new_cli_invocation(self):
        executable = Path(
            r"C:\Program Files\Bambu Studio\bambu-studio.exe"
        )
        with patch.object(
            Path,
            "is_file",
            return_value=True,
        ), patch.object(
            h,
            "_windows_bambu_process_ids",
            return_value={1234},
        ), patch.object(h.subprocess, "run") as mocked:
            with self.assertRaises(h.BambuHeadlessCliError):
                h.run_bambu_cli([str(executable), "--help"])

        mocked.assert_not_called()


    def test_production_modules_do_not_bypass_headless_runner(
        self,
    ):
        package_root = Path(h.__file__).resolve().parent
        violations = []

        for path in package_root.glob("*.py"):
            if path.name == "bambu_headless_cli.py":
                continue

            text = path.read_text(
                encoding="utf-8-sig"
            )

            if (
                "subprocess.run(" in text
                or "subprocess.Popen(" in text
            ):
                violations.append(path.name)

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()

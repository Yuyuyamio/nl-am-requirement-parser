import os
import subprocess
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
        ) as mocked:

            result = h.run_bambu_cli(
                [
                    str(executable),
                    "--help",
                ]
            )

        self.assertTrue(
            result.success
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
        ):

            result = h.run_bambu_cli(
                [
                    str(executable),
                    "--slice",
                    "0",
                ],
                expected_outputs=[
                    Path(
                        r"C:\definitely_missing"
                        r"\output.3mf"
                    )
                ],
            )

        self.assertFalse(
            result.success
        )

        self.assertFalse(
            result.outputs_exist
        )


if __name__ == "__main__":
    unittest.main()

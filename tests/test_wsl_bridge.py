from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from am_model_generator.contracts import (
    M2ProviderError,
)
from am_model_generator.wsl_bridge import (
    WslBridge,
)


class WslBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = WslBridge(
            linux_home="/home/fishcan",
            timeout_seconds=30,
        )

    def test_builds_direct_exec_command(
        self,
    ) -> None:
        command = self.bridge.build_command(
            environment_name="t2i",
            script_path=(
                "/home/fishcan/workspace/"
                "triposg_worker/"
                "run_reference_image_worker.py"
            ),
            arguments=[
                "--prompt",
                "a test object",
                "--output-path",
                "/tmp/reference.png",
            ],
        )

        self.assertEqual(
            command,
            [
                "wsl.exe",
                "--exec",
                (
                    "/home/fishcan/miniforge3/"
                    "envs/t2i/bin/python"
                ),
                (
                    "/home/fishcan/workspace/"
                    "triposg_worker/"
                    "run_reference_image_worker.py"
                ),
                "--prompt",
                "a test object",
                "--output-path",
                "/tmp/reference.png",
            ],
        )

    @patch(
        "am_model_generator.wsl_bridge."
        "subprocess.run"
    )
    def test_returns_completed_json_payload(
        self,
        mocked_run,
    ) -> None:
        payload = {
            "status": "completed",
            "output_image": "/tmp/reference.png",
        }

        mocked_run.return_value = (
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        )

        result = self.bridge.run_json_worker(
            environment_name="t2i",
            script_path="/worker.py",
            arguments=[
                "--output-path",
                "/tmp/reference.png",
            ],
        )

        self.assertEqual(
            result.payload,
            payload,
        )

        self.assertEqual(
            result.return_code,
            0,
        )

        called_command = (
            mocked_run.call_args.args[0]
        )

        self.assertIn(
            "--output-path",
            called_command,
        )

        self.assertIn(
            "/tmp/reference.png",
            called_command,
        )

    @patch(
        "am_model_generator.wsl_bridge."
        "subprocess.run"
    )
    def test_nonzero_worker_exit_is_structured_error(
        self,
        mocked_run,
    ) -> None:
        worker_payload = {
            "status": "failed",
            "message": "test failure",
        }

        mocked_run.return_value = (
            subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout=json.dumps(
                    worker_payload
                ),
                stderr="worker stderr",
            )
        )

        with self.assertRaises(
            M2ProviderError
        ) as captured:
            self.bridge.run_json_worker(
                environment_name="triposg",
                script_path="/worker.py",
            )

        self.assertEqual(
            captured.exception.code,
            "M2_WSL_WORKER_FAILED",
        )

        self.assertEqual(
            captured.exception.details[
                "worker_payload"
            ],
            worker_payload,
        )

    @patch(
        "am_model_generator.wsl_bridge."
        "subprocess.run"
    )
    def test_success_without_json_is_rejected(
        self,
        mocked_run,
    ) -> None:
        mocked_run.return_value = (
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="not json",
                stderr="",
            )
        )

        with self.assertRaises(
            M2ProviderError
        ) as captured:
            self.bridge.run_json_worker(
                environment_name="t2i",
                script_path="/worker.py",
            )

        self.assertEqual(
            captured.exception.code,
            "M2_WSL_WORKER_JSON_INVALID",
        )

    def test_rejects_relative_script_path(
        self,
    ) -> None:
        with self.assertRaises(
            ValueError
        ):
            self.bridge.build_command(
                environment_name="t2i",
                script_path=(
                    "relative/worker.py"
                ),
            )

    def test_rejects_unsafe_environment_name(
        self,
    ) -> None:
        with self.assertRaises(
            ValueError
        ):
            self.bridge.python_executable(
                "t2i;rm -rf /"
            )


if __name__ == "__main__":
    unittest.main()

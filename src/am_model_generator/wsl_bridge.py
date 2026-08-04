from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from am_model_generator.contracts import M2ProviderError


_ENVIRONMENT_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9_.-]+$"
)


@dataclass(frozen=True)
class WslWorkerResult:
    """
    A completed WSL worker invocation.

    The command is retained for auditability. It never contains
    credentials because local workers must not receive API keys.
    """

    command: tuple[str, ...]
    payload: dict[str, Any]
    stdout: str
    stderr: str
    return_code: int


class WslBridge:
    """
    Invoke a Python worker in a named Conda environment through WSL.

    This deliberately uses:

        wsl.exe --exec /absolute/path/to/python worker.py ...

    It does not invoke Bash and does not call ``conda activate``.
    Therefore PowerShell, Bash, CRLF, and nested-quote handling cannot
    alter individual worker arguments.
    """

    def __init__(
        self,
        *,
        linux_home: str = "/home/fishcan",
        conda_root: str | None = None,
        distribution: str | None = None,
        executable: str = "wsl.exe",
        timeout_seconds: float = 1800.0,
    ) -> None:
        self._linux_home = self._normalize_absolute_linux_path(
            linux_home,
            field_name="linux_home",
        )

        if conda_root is None:
            conda_root = str(
                PurePosixPath(self._linux_home)
                / "miniforge3"
            )

        self._conda_root = self._normalize_absolute_linux_path(
            conda_root,
            field_name="conda_root",
        )

        if distribution is not None:
            cleaned_distribution = distribution.strip()

            if not cleaned_distribution:
                raise ValueError(
                    "distribution must not be empty"
                )

            self._distribution = cleaned_distribution
        else:
            self._distribution = None

        cleaned_executable = executable.strip()

        if not cleaned_executable:
            raise ValueError(
                "executable must not be empty"
            )

        self._executable = cleaned_executable
        self._timeout_seconds = float(
            timeout_seconds
        )

        if self._timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

    @staticmethod
    def _normalize_absolute_linux_path(
        value: str,
        *,
        field_name: str,
    ) -> str:
        cleaned = value.strip()

        if (
            not cleaned
            or not cleaned.startswith("/")
        ):
            raise ValueError(
                f"{field_name} must be an absolute Linux path"
            )

        return str(PurePosixPath(cleaned))

    @staticmethod
    def _normalize_environment_name(
        value: str,
    ) -> str:
        cleaned = value.strip()

        if not _ENVIRONMENT_NAME_PATTERN.fullmatch(
            cleaned
        ):
            raise ValueError(
                "environment_name contains unsupported characters"
            )

        return cleaned

    def python_executable(
        self,
        environment_name: str,
    ) -> str:
        normalized_name = (
            self._normalize_environment_name(
                environment_name
            )
        )

        return str(
            PurePosixPath(self._conda_root)
            / "envs"
            / normalized_name
            / "bin"
            / "python"
        )

    def build_command(
        self,
        *,
        environment_name: str,
        script_path: str,
        arguments: Sequence[str] = (),
    ) -> list[str]:
        normalized_script_path = (
            self._normalize_absolute_linux_path(
                script_path,
                field_name="script_path",
            )
        )

        command = [
            self._executable,
        ]

        if self._distribution is not None:
            command.extend(
                [
                    "--distribution",
                    self._distribution,
                ]
            )

        command.extend(
            [
                "--exec",
                self.python_executable(
                    environment_name
                ),
                normalized_script_path,
            ]
        )

        for argument in arguments:
            if not isinstance(argument, str):
                raise TypeError(
                    "all worker arguments must be strings"
                )

            command.append(argument)

        return command

    @staticmethod
    def _parse_json_stdout(
        stdout: str,
    ) -> dict[str, Any] | None:
        cleaned = stdout.strip()

        if not cleaned:
            return None

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        return payload

    def run_json_worker(
        self,
        *,
        environment_name: str,
        script_path: str,
        arguments: Sequence[str] = (),
        timeout_seconds: float | None = None,
    ) -> WslWorkerResult:
        command = self.build_command(
            environment_name=environment_name,
            script_path=script_path,
            arguments=arguments,
        )

        effective_timeout = (
            self._timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )

        if effective_timeout <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
            )
        except FileNotFoundError as error:
            raise M2ProviderError(
                "M2_WSL_EXECUTABLE_NOT_FOUND",
                "找不到WSL可执行程序",
                details={
                    "executable": self._executable,
                    "network_called": False,
                },
            ) from error
        except subprocess.TimeoutExpired as error:
            raise M2ProviderError(
                "M2_WSL_WORKER_TIMEOUT",
                "WSL本地生成Worker执行超时",
                details={
                    "timeout_seconds": effective_timeout,
                    "command": command,
                    "network_called": False,
                },
            ) from error
        except OSError as error:
            raise M2ProviderError(
                "M2_WSL_PROCESS_START_FAILED",
                "无法启动WSL本地生成Worker",
                details={
                    "reason": str(error),
                    "command": command,
                    "network_called": False,
                },
            ) from error

        payload = self._parse_json_stdout(
            process.stdout
        )

        if process.returncode != 0:
            details: dict[str, Any] = {
                "return_code": process.returncode,
                "command": command,
                "stdout_tail": process.stdout[-4000:],
                "stderr_tail": process.stderr[-4000:],
                "network_called": False,
            }

            if payload is not None:
                details["worker_payload"] = payload

            raise M2ProviderError(
                "M2_WSL_WORKER_FAILED",
                "WSL本地生成Worker执行失败",
                details=details,
            )

        if payload is None:
            raise M2ProviderError(
                "M2_WSL_WORKER_JSON_INVALID",
                "WSL Worker没有返回有效的JSON对象",
                details={
                    "return_code": process.returncode,
                    "command": command,
                    "stdout_tail": process.stdout[-4000:],
                    "stderr_tail": process.stderr[-4000:],
                    "network_called": False,
                },
            )

        if payload.get("status") == "failed":
            raise M2ProviderError(
                "M2_WSL_WORKER_REPORTED_FAILURE",
                "WSL Worker报告生成失败",
                details={
                    "command": command,
                    "worker_payload": payload,
                    "stderr_tail": process.stderr[-4000:],
                    "network_called": False,
                },
            )

        return WslWorkerResult(
            command=tuple(command),
            payload=payload,
            stdout=process.stdout,
            stderr=process.stderr,
            return_code=process.returncode,
        )

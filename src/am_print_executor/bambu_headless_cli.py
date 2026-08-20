from __future__ import annotations

import os
import subprocess

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class BambuHeadlessCliError(RuntimeError):
    pass


@dataclass(frozen=True)
class BambuCliResult:
    command: tuple[str, ...]
    raw_exit: int
    signed_exit: int
    stdout: str
    stderr: str
    expected_outputs: tuple[str, ...]
    outputs_exist: bool
    success: bool


def normalise_windows_exit(
    code: int,
) -> int:
    """
    Bambu Studio may surface Windows process exit values
    as unsigned 32-bit integers, e.g. 4294967290 == -6.
    """
    if code > 0x7FFFFFFF:
        return code - 0x100000000

    return code


def _windows_hidden_startup() -> tuple[
    subprocess.STARTUPINFO,
    int,
]:
    """
    Construct the strongest normal Python/Win32 process
    suppression used by this project.

    - SW_HIDE
    - STARTF_USESHOWWINDOW
    - CREATE_NO_WINDOW
    - no shell
    """
    if os.name != "nt":
        raise BambuHeadlessCliError(
            "Bambu headless Windows runner was invoked "
            "on a non-Windows platform."
        )

    startupinfo = subprocess.STARTUPINFO()

    startupinfo.dwFlags |= (
        subprocess.STARTF_USESHOWWINDOW
    )

    startupinfo.wShowWindow = (
        subprocess.SW_HIDE
    )

    creationflags = (
        subprocess.CREATE_NO_WINDOW
    )

    return (
        startupinfo,
        creationflags,
    )


def run_bambu_cli(
    command: Sequence[str],
    *,
    expected_outputs: Sequence[
        str | Path
    ] = (),
    cwd: str | Path | None = None,
    timeout: float | None = None,
) -> BambuCliResult:
    """
    The ONLY production entry point for invoking the
    installed Bambu Studio executable.

    This function must never intentionally display the
    Bambu Studio GUI.

    Success requires BOTH:
      1. signed process exit == 0
      2. every declared expected output exists

    Therefore a misleading Windows exit 0 without an
    output artifact can never be accepted.
    """
    args = tuple(
        str(value)
        for value in command
    )

    if not args:
        raise BambuHeadlessCliError(
            "Empty Bambu CLI command."
        )

    executable = Path(
        args[0]
    ).expanduser()

    if (
        executable.name.lower()
        != "bambu-studio.exe"
    ):
        raise BambuHeadlessCliError(
            "Unexpected Bambu executable: "
            + str(executable)
        )

    if not executable.is_file():
        raise BambuHeadlessCliError(
            "Bambu Studio executable missing: "
            + str(executable)
        )

    startupinfo, creationflags = (
        _windows_hidden_startup()
    )

    completed = subprocess.run(
        args,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=(
            None
            if cwd is None
            else str(
                Path(cwd).resolve()
            )
        ),
        timeout=timeout,
        startupinfo=startupinfo,
        creationflags=creationflags,
        check=False,
    )

    raw_exit = int(
        completed.returncode
    )

    signed_exit = (
        normalise_windows_exit(
            raw_exit
        )
    )

    resolved_outputs = tuple(
        str(
            Path(path)
            .expanduser()
            .resolve()
        )
        for path in expected_outputs
    )

    outputs_exist = all(
        Path(path).is_file()
        for path in resolved_outputs
    )

    success = (
        signed_exit == 0
        and outputs_exist
    )

    return BambuCliResult(
        command=args,
        raw_exit=raw_exit,
        signed_exit=signed_exit,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        expected_outputs=
            resolved_outputs,
        outputs_exist=
            outputs_exist,
        success=
            success,
    )


def require_bambu_cli_success(
    result: BambuCliResult,
) -> BambuCliResult:
    if result.success:
        return result

    raise BambuHeadlessCliError(
        "Headless Bambu CLI failed: "
        f"signed_exit={result.signed_exit}, "
        f"outputs_exist={result.outputs_exist}"
    )

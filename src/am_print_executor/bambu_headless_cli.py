from __future__ import annotations

import ctypes
import os
import subprocess
import threading
import time

from contextlib import contextmanager
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
    lock_wait_seconds: float
    process_settle_seconds: float


_LOCAL_PROCESS_LOCK = threading.RLock()
_WINDOWS_MUTEX_NAME = (
    r"Local\NL_AM_BambuStudio_Headless_CLI"
)


def _windows_bambu_process_ids() -> set[int]:
    """Return every live Bambu Studio PID without external dependencies."""

    if os.name != "nt":
        return set()

    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (
        wintypes.DWORD,
        wintypes.DWORD,
    )
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32W),
    )
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32W),
    )
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot in {None, invalid_handle}:
        raise BambuHeadlessCliError(
            "Could not inspect Bambu process state: "
            f"winerror={ctypes.get_last_error()}"
        )

    result: set[int] = set()
    entry = ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        present = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            if entry.szExeFile.lower() in {
                "bambu-studio.exe",
                "bambustudio.exe",
            }:
                result.add(int(entry.th32ProcessID))
            present = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def _wait_for_spawned_bambu_processes(
    baseline: set[int],
    *,
    timeout: float,
    quiet_seconds: float = 0.25,
) -> float:
    """Keep the global mutex until detached Bambu workers have exited."""

    started = time.monotonic()
    deadline = started + max(0.0, timeout)
    quiet_started: float | None = None

    while True:
        current = _windows_bambu_process_ids()
        spawned = current - baseline
        now = time.monotonic()

        if not spawned:
            if quiet_started is None:
                quiet_started = now
            if now - quiet_started >= quiet_seconds:
                return now - started
        else:
            quiet_started = None

        if now >= deadline:
            raise BambuHeadlessCliError(
                "Bambu CLI produced output but detached headless workers "
                "did not exit before the settle timeout: "
                f"pids={sorted(spawned)} timeout={timeout}."
            )
        time.sleep(0.1)


@contextmanager
def _exclusive_local_thread(
    timeout: float | None,
):
    acquired = (
        _LOCAL_PROCESS_LOCK.acquire()
        if timeout is None
        else _LOCAL_PROCESS_LOCK.acquire(
            timeout=max(0.0, timeout)
        )
    )

    if not acquired:
        raise BambuHeadlessCliError(
            "Timed out waiting for local Bambu CLI access."
        )

    try:
        yield
    finally:
        _LOCAL_PROCESS_LOCK.release()


@contextmanager
def _exclusive_bambu_process(
    timeout: float | None,
):
    """Serialise Bambu Studio across threads and processes.

    Bambu Studio owns shared per-user state even in CLI mode.  Running two
    independent export operations against that state is therefore unsafe.
    A Windows named mutex covers every NL-AM Python process, while the local
    lock keeps the fallback deterministic on other platforms and in tests.
    """

    started = time.monotonic()

    with _exclusive_local_thread(timeout):
        if os.name != "nt":
            yield time.monotonic() - started
            return

        from ctypes import wintypes

        kernel32 = ctypes.WinDLL(
            "kernel32",
            use_last_error=True,
        )
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = (
            wintypes.HANDLE
        )
        kernel32.WaitForSingleObject.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
        )
        kernel32.WaitForSingleObject.restype = (
            wintypes.DWORD
        )
        kernel32.ReleaseMutex.argtypes = (
            wintypes.HANDLE,
        )
        kernel32.ReleaseMutex.restype = (
            wintypes.BOOL
        )
        kernel32.CloseHandle.argtypes = (
            wintypes.HANDLE,
        )
        kernel32.CloseHandle.restype = (
            wintypes.BOOL
        )

        handle = kernel32.CreateMutexW(
            None,
            False,
            _WINDOWS_MUTEX_NAME,
        )

        if not handle:
            raise BambuHeadlessCliError(
                "Could not create the Bambu CLI process mutex: "
                f"winerror={ctypes.get_last_error()}"
            )

        infinite = 0xFFFFFFFF
        remaining_timeout = (
            None
            if timeout is None
            else max(
                0.0,
                timeout
                - (time.monotonic() - started),
            )
        )
        wait_ms = (
            infinite
            if remaining_timeout is None
            else max(
                0,
                min(
                    int(remaining_timeout * 1000),
                    infinite - 1,
                ),
            )
        )
        wait_result = kernel32.WaitForSingleObject(
            handle,
            wait_ms,
        )

        wait_object_0 = 0x00000000
        wait_abandoned = 0x00000080

        if wait_result not in {
            wait_object_0,
            wait_abandoned,
        }:
            kernel32.CloseHandle(handle)
            detail = (
                "timeout"
                if wait_result == 0x00000102
                else f"winerror={ctypes.get_last_error()}"
            )
            raise BambuHeadlessCliError(
                "Timed out waiting for exclusive Bambu CLI access: "
                + detail
            )

        try:
            yield time.monotonic() - started
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)


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
    lock_timeout: float | None = None,
    process_settle_timeout: float = 180.0,
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

    if executable.name.lower() not in {
        "bambu-studio.exe",
        "bambustudio.exe",
    }:
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

    effective_lock_timeout = (
        timeout
        if lock_timeout is None
        else lock_timeout
    )

    if process_settle_timeout <= 0:
        raise BambuHeadlessCliError(
            "process_settle_timeout must be positive."
        )

    with _exclusive_bambu_process(
        effective_lock_timeout
    ) as lock_wait_seconds:
        baseline_processes = _windows_bambu_process_ids()
        if baseline_processes:
            raise BambuHeadlessCliError(
                "Bambu Studio is already running. Close the existing UI or "
                "wait for the prior headless worker before automatic CLI use: "
                f"pids={sorted(baseline_processes)}."
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
        process_settle_seconds = _wait_for_spawned_bambu_processes(
            baseline_processes,
            timeout=process_settle_timeout,
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
        lock_wait_seconds=round(
            lock_wait_seconds,
            6,
        ),
        process_settle_seconds=round(
            process_settle_seconds,
            6,
        ),
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

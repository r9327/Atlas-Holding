"""Cross-platform bounded process-tree execution for trusted local adapters."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


CREATE_SUSPENDED = 0x00000004


class BoundedProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class _WindowsJob:
    """Kill-on-close Job; child starts suspended so it cannot escape first."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise BoundedProcessError("Windows Job Objects require Windows")
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32First.restype = wintypes.BOOL
        kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32Next.restype = wintypes.BOOL
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        self._kernel = kernel
        self._entry = ThreadEntry
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise BoundedProcessError(
                f"CreateJobObjectW failed with WinError {ctypes.get_last_error()}"
            )
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel.SetInformationJobObject(
                self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise BoundedProcessError(
                f"SetInformationJobObject failed with WinError {error}"
            )

    def attach_and_resume(self, process: subprocess.Popen) -> None:
        import ctypes

        # This child is CREATE_SUSPENDED. Popen.poll() is not an
        # authoritative pre-attach gate; Windows must decide from the handle.
        if self._handle is None or not self._kernel.AssignProcessToJobObject(
                self._handle, process._handle):
            raise BoundedProcessError(
                f"AssignProcessToJobObject failed with WinError {ctypes.get_last_error()}"
            )
        snapshot = self._kernel.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise BoundedProcessError(
                f"Thread snapshot failed with WinError {ctypes.get_last_error()}"
            )
        resumed = 0
        try:
            entry = self._entry()
            entry.dwSize = ctypes.sizeof(entry)
            more = bool(self._kernel.Thread32First(snapshot, ctypes.byref(entry)))
            while more:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self._kernel.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise BoundedProcessError(
                            f"OpenThread failed with WinError {ctypes.get_last_error()}"
                        )
                    try:
                        if self._kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise BoundedProcessError(
                                f"ResumeThread failed with WinError {ctypes.get_last_error()}"
                            )
                        resumed += 1
                    finally:
                        self._kernel.CloseHandle(thread)
                more = bool(self._kernel.Thread32Next(snapshot, ctypes.byref(entry)))
        finally:
            self._kernel.CloseHandle(snapshot)
        if resumed == 0:
            raise BoundedProcessError("Suspended process had no resumable thread")

    def terminate(self) -> None:
        if self._handle is not None:
            self._kernel.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def run_bounded(
    argv: tuple[str, ...], *, cwd: Path, environment: dict[str, str],
    timeout_seconds: float, stdout_limit: int, stderr_limit: int,
) -> ProcessResult:
    """Execute fixed argv without a shell and leave no descendant alive."""
    if (type(argv) is not tuple or not argv
            or any(type(item) is not str or not item or "\x00" in item for item in argv)
            or not isinstance(cwd, Path) or not cwd.is_absolute() or not cwd.is_dir()
            or type(environment) is not dict
            or any(type(k) is not str or type(v) is not str for k, v in environment.items())
            or not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not 0.05 <= timeout_seconds <= 3600
            or type(stdout_limit) is not int or not 1 <= stdout_limit <= 16 * 1024 * 1024
            or type(stderr_limit) is not int or not 1 <= stderr_limit <= 4 * 1024 * 1024):
        raise BoundedProcessError("Invalid bounded process request")
    job = _WindowsJob() if os.name == "nt" else None
    process = None
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, shell=False,
                start_new_session=(os.name != "nt"),
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | CREATE_SUSPENDED
                    if os.name == "nt" else 0
                ),
            )
            if job is not None:
                job.attach_and_resume(process)
            started = time.monotonic()
            while process.poll() is None:
                if time.monotonic() - started > timeout_seconds:
                    raise BoundedProcessError("Process timed out")
                if os.fstat(stdout.fileno()).st_size > stdout_limit:
                    raise BoundedProcessError("Process stdout exceeded size limit")
                if os.fstat(stderr.fileno()).st_size > stderr_limit:
                    raise BoundedProcessError("Process stderr exceeded size limit")
                time.sleep(0.02)
            if os.fstat(stdout.fileno()).st_size > stdout_limit:
                raise BoundedProcessError("Process stdout exceeded size limit")
            if os.fstat(stderr.fileno()).st_size > stderr_limit:
                raise BoundedProcessError("Process stderr exceeded size limit")
            stdout.seek(0)
            stderr.seek(0)
            return ProcessResult(process.returncode, stdout.read(), stderr.read())
        except BaseException:
            if job is not None:
                job.terminate()
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            raise
        finally:
            if job is not None:
                job.close()
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()

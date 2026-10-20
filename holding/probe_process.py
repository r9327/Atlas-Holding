"""Bounded subprocess execution for read-only dependency probes.

The direct process and every child it starts share one disposable containment
boundary: a process group on POSIX and a kill-on-close Job Object on Windows.
"""
from __future__ import annotations

import os
import signal
import subprocess


CREATE_SUSPENDED = 0x00000004


class ProbeProcessError(OSError):
    """The probe could not be started inside a verified process boundary."""


class _WindowsJob:
    """Minimal kill-on-close Job Object for a single probe tree."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ProbeProcessError("Windows Job Objects require Windows")
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
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
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
        self._thread_entry_type = ThreadEntry
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ProbeProcessError(
                f"CreateJobObjectW failed with WinError {ctypes.get_last_error()}"
            )
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
                self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ProbeProcessError(
                f"SetInformationJobObject failed with WinError {error}"
            )

    def attach_and_resume(self, process: subprocess.Popen[str]) -> None:
        import ctypes

        if process.poll() is not None or not self._kernel.AssignProcessToJobObject(
                self._handle, process._handle):
            error = ctypes.get_last_error()
            raise ProbeProcessError(
                f"AssignProcessToJobObject failed with WinError {error}"
            )
        snapshot = self._kernel.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ProbeProcessError(
                f"Thread snapshot failed with WinError {ctypes.get_last_error()}"
            )
        resumed = 0
        try:
            entry = self._thread_entry_type()
            entry.dwSize = ctypes.sizeof(entry)
            more = bool(self._kernel.Thread32First(snapshot, ctypes.byref(entry)))
            while more:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self._kernel.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise ProbeProcessError(
                            f"OpenThread failed with WinError {ctypes.get_last_error()}"
                        )
                    try:
                        if self._kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ProbeProcessError(
                                f"ResumeThread failed with WinError {ctypes.get_last_error()}"
                            )
                        resumed += 1
                    finally:
                        self._kernel.CloseHandle(thread)
                more = bool(self._kernel.Thread32Next(snapshot, ctypes.byref(entry)))
        finally:
            self._kernel.CloseHandle(snapshot)
        if resumed == 0:
            raise ProbeProcessError("Suspended probe had no resumable thread")

    def terminate(self) -> None:
        if self._handle is not None:
            self._kernel.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def run_probe(argv: list[str], *, environment: dict[str, str],
              timeout: float) -> subprocess.CompletedProcess[str]:
    """Run one probe and leave no surviving process from its process tree."""
    job = _WindowsJob() if os.name == "nt" else None
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
            env=environment,
            shell=False,
            start_new_session=(os.name != "nt"),
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | CREATE_SUSPENDED
                if os.name == "nt" else 0
            ),
        )
        if job is not None:
            job.attach_and_resume(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if job is not None:
                job.terminate()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        if job is not None:
            job.close()
        elif process is not None:
            # A successful direct child may still have launched a background
            # helper. Dispose of the whole probe group before returning.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()

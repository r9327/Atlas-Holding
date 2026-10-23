"""Windows Job Object ownership for explicitly authorized local MCP hub children.

Start order is fail-closed: CREATE_SUSPENDED -> AssignProcessToJobObject ->
ResumeThread. Job memory/child limits are set before launch; closing the last
job handle kills its process tree. No breakaway flags are granted.

This is resource containment, *not* a Windows security sandbox: a Job Object
does not prohibit network, file I/O or malicious code. External untrusted
plugins must additionally pass independent signature, ACL, account and policy
gates before they can be launched.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys

from .mcp_local_inventory import PinnedLocalPlugin, inspect_pinned_plugin

CREATE_SUSPENDED = 0x00000004
KILL_ON_JOB_CLOSE = 0x00002000
JOB_MEMORY_LIMIT = 0x00000200
ACTIVE_PROCESS_LIMIT = 0x00000008
_JOB_EXTENDED_INFO = 9
_THREAD_SUSPEND_RESUME = 0x0002
_TH32CS_SNAPTHREAD = 0x00000004
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class WindowsJobError(RuntimeError):
    pass


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _kernel32():
    if os.name != "nt":
        raise WindowsJobError("Windows Job Objects are only available on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    return kernel32


def _win_error(stage: str) -> WindowsJobError:
    # No paths or secrets in exception text.
    return WindowsJobError(f"Windows Job Object {stage} failed")


def _create_job(kernel, *, job_memory_mb: int, active_limit: int) -> int:
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _win_error("creation")
    try:
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = (
            KILL_ON_JOB_CLOSE | JOB_MEMORY_LIMIT | ACTIVE_PROCESS_LIMIT)
        limits.BasicLimitInformation.ActiveProcessLimit = active_limit
        limits.JobMemoryLimit = job_memory_mb * 1024 * 1024
        if not kernel.SetInformationJobObject(
                job, _JOB_EXTENDED_INFO, ctypes.byref(limits),
                ctypes.sizeof(limits)):
            raise _win_error("limits")
        return job
    except BaseException:
        kernel.CloseHandle(job)
        raise


def _assign(kernel, job: int, pid: int) -> None:
    process = kernel.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE
        | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise _win_error("process access")
    try:
        if not kernel.AssignProcessToJobObject(job, process):
            raise _win_error("assignment")
    finally:
        kernel.CloseHandle(process)


def _resume_suspended_child(kernel, pid: int) -> None:
    snapshot = kernel.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        raise _win_error("thread enumeration")
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == pid:
                handle = kernel.OpenThread(
                    _THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if not handle:
                    raise _win_error("thread access")
                try:
                    previous = kernel.ResumeThread(handle)
                    if previous == 0xFFFFFFFF or previous != 1:
                        raise _win_error("thread resume")
                    return
                finally:
                    kernel.CloseHandle(handle)
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        raise _win_error("missing primary thread")
    finally:
        kernel.CloseHandle(snapshot)


@dataclass
class ManagedHubProcess:
    process: subprocess.Popen
    job_handle: int
    _kernel: object
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Job kill is stronger than terminating only the Python subprocess.
        self._kernel.CloseHandle(self.job_handle)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # The job handles descendants; fail closed for this child too.
            self.process.kill()
            self.process.wait(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def launch_verified_local_hub(*, interpreter_pin: PinnedLocalPlugin,
                              package_root: Path, job_memory_mb: int = 256,
                              active_limit: int = 4) -> ManagedHubProcess:
    """Launch only this installation's own internal stdio hub.

    Windows-only; the trusted supervisor must protect package files and
    pin manifests with private Windows ACLs. No arbitrary plugin executable
    or agent-controlled arguments are accepted at this boundary.
    """
    if os.name != "nt":
        raise WindowsJobError("Windows Job Objects are only available on Windows")
    if (type(interpreter_pin) is not PinnedLocalPlugin
            or not isinstance(package_root, Path) or not package_root.is_absolute()
            or type(job_memory_mb) is not int or not 64 <= job_memory_mb <= 1024
            or type(active_limit) is not int or not 1 <= active_limit <= 8
            or package_root.resolve() != Path(__file__).resolve().parent.parent
            or interpreter_pin.executable.resolve() != Path(sys.executable).resolve()):
        raise ValueError("Untrusted local hub launch configuration")
    if inspect_pinned_plugin(interpreter_pin).binary_state != "BINARY_VERIFIED":
        raise WindowsJobError("Pinned Python runtime is not verified")
    kernel = _kernel32()
    job = _create_job(kernel, job_memory_mb=job_memory_mb,
                      active_limit=active_limit)
    child = None
    try:
        env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP"):
            if key in os.environ:
                env[key] = os.environ[key]
        child = subprocess.Popen(
            [str(interpreter_pin.executable), "-B", "-m", "holding.mcp_entry"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=str(package_root), env=env, shell=False,
            creationflags=CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW,
        )
        # The child remains unable to execute code before assignment.
        _assign(kernel, job, child.pid)
        _resume_suspended_child(kernel, child.pid)
        return ManagedHubProcess(child, job, kernel)
    except BaseException:
        # Never return a detached or still-suspended child.
        if child is not None:
            child.kill()
            child.wait(timeout=5)
            for stream in (child.stdin, child.stdout):
                if stream is not None:
                    stream.close()
        kernel.CloseHandle(job)
        raise

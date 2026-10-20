"""Windows process-tree lifecycle using an owned Job Object.

A Job Object kills remaining children when its handle is closed. This does NOT
provide filesystem isolation or credential protection; a restricted Windows
account and ACLs are mandatory before running untrusted executables.
"""
from __future__ import annotations

import os


class WindowsJobError(RuntimeError):
    pass


class WindowsJob:
    def __init__(self, *, max_job_memory_bytes: int | None = None) -> None:
        if os.name != 'nt':
            raise WindowsJobError('Windows Job Objects are only available on Windows')
        if max_job_memory_bytes is not None and (
                type(max_job_memory_bytes) is not int
                or not 32 * 1024 ** 2 <= max_job_memory_bytes <= 1024 ** 3):
            raise WindowsJobError('Invalid bounded Windows Job memory quota')
        self.max_job_memory_bytes = max_job_memory_bytes
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_longlong),
                ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class IOCounters(ctypes.Structure):
            _fields_ = [(key, ctypes.c_ulonglong) for key in (
                'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', BasicLimits),
                ('IoInfo', IOCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        k32.QueryInformationJobObject.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        self._k32 = k32
        self._extended_limits_type = ExtendedLimits
        self._handle = k32.CreateJobObjectW(None, None)
        if not self._handle:
            raise WindowsJobError('CreateJobObjectW failed')
        limits = ExtendedLimits()
        # Always kill descendants on close; optionally limit committed memory
        # for the whole child Job (JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200).
        # This does not include separately hosted Ollama inference processes.
        limits.BasicLimitInformation.LimitFlags = 0x00002000
        if max_job_memory_bytes is not None:
            limits.BasicLimitInformation.LimitFlags |= 0x00000200
            limits.JobMemoryLimit = max_job_memory_bytes
        if not k32.SetInformationJobObject(self._handle, 9, ctypes.byref(limits),
                                           ctypes.sizeof(limits)):
            self.close()
            raise WindowsJobError('Could not configure kill-on-close Job Object')

    def attach(self, process) -> None:
        if self._handle is None or process.poll() is not None:
            raise WindowsJobError('Cannot attach stopped process to Windows Job Object')
        if not self._k32.AssignProcessToJobObject(self._handle, process._handle):
            raise WindowsJobError('AssignProcessToJobObject failed; refusing unsafe agent')

    def peak_job_memory_bytes(self) -> int:
        """Observed peak committed bytes in this child Job, not full Holding RAM."""
        if self._handle is None:
            raise WindowsJobError('Cannot query closed Windows Job Object')
        import ctypes
        from ctypes import wintypes
        info = self._extended_limits_type()
        returned = wintypes.DWORD()
        if not self._k32.QueryInformationJobObject(
                self._handle, 9, ctypes.byref(info), ctypes.sizeof(info),
                ctypes.byref(returned)):
            raise WindowsJobError('Could not query Windows Job memory peak')
        return int(info.PeakJobMemoryUsed)

    def terminate(self) -> None:
        if self._handle is not None and not self._k32.TerminateJobObject(self._handle, 1):
            raise WindowsJobError('TerminateJobObject failed')

    def close(self) -> None:
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

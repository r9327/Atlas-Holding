"""Windows process-tree lifecycle using an owned Job Object.

A Job Object kills remaining children when its handle is closed. This does NOT
provide filesystem isolation or credential protection; a restricted Windows
account and ACLs are mandatory before running untrusted executables.
"""
from __future__ import annotations

import os

CREATE_SUSPENDED = 0x00000004


class WindowsJobError(RuntimeError):
    def __init__(self, message: str, *, code: str = 'WINDOWS_JOB_ERROR') -> None:
        super().__init__(message)
        self.code = code


class WindowsJob:
    def __init__(self, *, max_job_memory_bytes: int | None = None) -> None:
        if os.name != 'nt':
            raise WindowsJobError(
                'Windows Job Objects are only available on Windows',
                code='UNSUPPORTED_PLATFORM',
            )
        if max_job_memory_bytes is not None and (
                type(max_job_memory_bytes) is not int
                or not 32 * 1024 ** 2 <= max_job_memory_bytes <= 1024 ** 3):
            raise WindowsJobError(
                'Invalid bounded Windows Job memory quota',
                code='INVALID_MEMORY_QUOTA',
            )
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

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ('dwSize', wintypes.DWORD),
                ('cntUsage', wintypes.DWORD),
                ('th32ThreadID', wintypes.DWORD),
                ('th32OwnerProcessID', wintypes.DWORD),
                ('tpBasePri', wintypes.LONG),
                ('tpDeltaPri', wintypes.LONG),
                ('dwFlags', wintypes.DWORD),
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
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        k32.Thread32First.restype = wintypes.BOOL
        k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        k32.Thread32Next.restype = wintypes.BOOL
        k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenThread.restype = wintypes.HANDLE
        k32.ResumeThread.argtypes = [wintypes.HANDLE]
        k32.ResumeThread.restype = wintypes.DWORD
        self._k32 = k32
        self._extended_limits_type = ExtendedLimits
        self._thread_entry_type = ThreadEntry
        self._handle = k32.CreateJobObjectW(None, None)
        if not self._handle:
            error = ctypes.get_last_error()
            raise WindowsJobError(
                f'CreateJobObjectW failed with WinError {error}',
                code=f'CREATE_JOB_{error}',
            )
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
            error = ctypes.get_last_error()
            self.close()
            raise WindowsJobError(
                f'Could not configure kill-on-close Job Object: WinError {error}',
                code=f'CONFIGURE_JOB_{error}',
            )

    def attach(self, process) -> None:
        """Attach an already running process (legacy callers only).

        New child launches must use :meth:`attach_suspended` to avoid the
        start-before-attach race.
        """
        if self._handle is None or process.poll() is not None:
            raise WindowsJobError(
                'Cannot attach stopped process to Windows Job Object',
                code='PROCESS_EXITED_BEFORE_ATTACH',
            )
        self._assign_process(process)

    def _assign_process(self, process) -> None:
        if self._handle is None:
            raise WindowsJobError(
                'Cannot attach process to a closed Windows Job Object',
                code='ASSIGN_CLOSED_JOB',
            )
        if not self._k32.AssignProcessToJobObject(self._handle, process._handle):
            import ctypes
            error = ctypes.get_last_error()
            raise WindowsJobError(
                f'AssignProcessToJobObject failed with WinError {error}; refusing unsafe agent',
                code=f'ASSIGN_PROCESS_{error}',
            )

    def attach_suspended(self, process) -> None:
        """Attach a CREATE_SUSPENDED child, then resume all of its threads.

        Do not poll() a suspended child before assigning it. The Win32
        process handle and AssignProcessToJobObject are authoritative: a
        pre-assignment poll is a TOCTOU check, not a protection boundary.
        If assignment fails, the child is never resumed and the caller's
        cleanup terminates it (fail closed).
        """
        self._assign_process(process)
        import ctypes
        snapshot = self._k32.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            raise WindowsJobError(
                f'Could not enumerate suspended process threads: WinError {error}',
                code=f'SNAPSHOT_THREADS_{error}',
            )
        resumed = 0
        try:
            entry = self._thread_entry_type()
            entry.dwSize = ctypes.sizeof(entry)
            more = bool(self._k32.Thread32First(snapshot, ctypes.byref(entry)))
            while more:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self._k32.OpenThread(0x0002, False, entry.th32ThreadID)
                    if not thread:
                        error = ctypes.get_last_error()
                        raise WindowsJobError(
                            f'Could not open suspended process thread: WinError {error}',
                            code=f'OPEN_THREAD_{error}',
                        )
                    try:
                        if self._k32.ResumeThread(thread) == 0xFFFFFFFF:
                            error = ctypes.get_last_error()
                            raise WindowsJobError(
                                f'Could not resume suspended process: WinError {error}',
                                code=f'RESUME_THREAD_{error}',
                            )
                        resumed += 1
                    finally:
                        self._k32.CloseHandle(thread)
                more = bool(self._k32.Thread32Next(snapshot, ctypes.byref(entry)))
        finally:
            self._k32.CloseHandle(snapshot)
        if resumed == 0:
            raise WindowsJobError(
                'Suspended process had no resumable thread',
                code='NO_SUSPENDED_THREAD',
            )

    def peak_job_memory_bytes(self) -> int:
        """Observed peak committed bytes in this child Job, not full Holding RAM."""
        if self._handle is None:
            raise WindowsJobError(
                'Cannot query closed Windows Job Object',
                code='QUERY_CLOSED_JOB',
            )
        import ctypes
        from ctypes import wintypes
        info = self._extended_limits_type()
        returned = wintypes.DWORD()
        if not self._k32.QueryInformationJobObject(
                self._handle, 9, ctypes.byref(info), ctypes.sizeof(info),
                ctypes.byref(returned)):
            error = ctypes.get_last_error()
            raise WindowsJobError(
                f'Could not query Windows Job memory peak: WinError {error}',
                code=f'QUERY_JOB_{error}',
            )
        return int(info.PeakJobMemoryUsed)

    def terminate(self) -> None:
        if self._handle is not None and not self._k32.TerminateJobObject(self._handle, 1):
            import ctypes
            error = ctypes.get_last_error()
            raise WindowsJobError(
                f'TerminateJobObject failed with WinError {error}',
                code=f'TERMINATE_JOB_{error}',
            )

    def close(self) -> None:
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

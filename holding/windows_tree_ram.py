"""Windows-only process-tree working-set sampling for NONRELEASE RAM evidence.

Measure every visible descendant of specifically launched Holding PID roots, not
only the Qt window PID. This measures snapshots, not sub-interval transient
spikes, private committed bytes, or multi-hour production workloads.
Ollama inference executables are recorded separately, never silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import threading
from typing import Callable


@dataclass(frozen=True)
class ProcessMemory:
    pid: int
    parent_pid: int
    name: str
    working_set_bytes: int


@dataclass(frozen=True)
class MemorySample:
    holding_bytes: int
    model_bytes: int
    included_pids: tuple[int, ...]
    model_pids: tuple[int, ...]


# These explicit executable names are the ONLY allowed inference exclusions.
# Unknown children remain Holding memory (fail-safe conservative accounting).
_MODEL_BINARIES = frozenset({
    "ollama.exe", "ollama_llama_server.exe", "ollama-llama-server.exe",
})


def account_tree(
    processes: tuple[ProcessMemory, ...], roots: set[int],
) -> MemorySample:
    """A bounded, deterministic graph walk; each PID counted only once."""
    if not roots or any(type(pid) is not int or pid <= 0 for pid in roots):
        raise ValueError("Positive process roots are required")
    if len(processes) > 65536:
        raise ValueError("Unexpected process table size")
    by_pid: dict[int, ProcessMemory] = {}
    for process in processes:
        if (type(process) is not ProcessMemory
                or type(process.pid) is not int or process.pid <= 0
                or type(process.parent_pid) is not int
                or type(process.working_set_bytes) is not int
                or process.working_set_bytes < 0
                or not isinstance(process.name, str)
                or not process.name or process.pid in by_pid):
            raise ValueError("Malformed or duplicate process memory record")
        by_pid[process.pid] = process
    children: dict[int, list[int]] = {}
    for proc in processes:
        children.setdefault(proc.parent_pid, []).append(proc.pid)
    found: set[int] = set()
    pending = [pid for pid in roots if pid in by_pid]
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children.get(pid, ()))
    if not found:
        return MemorySample(0, 0, (), ())
    model = tuple(sorted(pid for pid in found
                         if by_pid[pid].name.lower() in _MODEL_BINARIES))
    model_set = set(model)
    holding = tuple(sorted(found - model_set))
    return MemorySample(
        sum(by_pid[pid].working_set_bytes for pid in holding),
        sum(by_pid[pid].working_set_bytes for pid in model),
        holding, model,
    )


def windows_process_snapshot() -> tuple[ProcessMemory, ...]:
    """Enumerate Windows processes and their working sets using Win32 APIs."""
    if os.name != "nt":
        raise OSError("Native Windows process accounting required")
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class MemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    handle = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError("Windows process snapshot unavailable")
    entries: list[tuple[int, int, str]] = []
    try:
        row = ProcessEntry()
        row.dwSize = ctypes.sizeof(row)
        found = bool(kernel.Process32FirstW(handle, ctypes.byref(row)))
        while found:
            entries.append((
                int(row.th32ProcessID), int(row.th32ParentProcessID),
                str(row.szExeFile),
            ))
            found = bool(kernel.Process32NextW(handle, ctypes.byref(row)))
    finally:
        kernel.CloseHandle(handle)

    # Opening system/security processes is not necessary for a measurement
    # rooted at our own engine and viewer; callers apply the tree filter.
    # Access-denied/unobservable processes stay UNKNOWN, not zero-valued.
    return tuple(
        ProcessMemory(pid, parent, name, -1)
        for pid, parent, name in entries if pid > 0
    )


def windows_tree_sample(roots: set[int]) -> MemorySample:
    """Read bytes for the identified subtree only, never all system PIDs."""
    import ctypes
    from ctypes import wintypes
    table = windows_process_snapshot()
    metadata = {p.pid: p for p in table}
    children: dict[int, list[int]] = {}
    for p in table:
        children.setdefault(p.parent_pid, []).append(p.pid)
    selected: set[int] = set()
    pending = [pid for pid in roots if pid in metadata]
    while pending:
        pid = pending.pop()
        if pid not in selected:
            selected.add(pid)
            pending.extend(children.get(pid, ()))
    if not selected:
        return MemorySample(0, 0, (), ())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            )
        ]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    measured: list[ProcessMemory] = []
    for pid in sorted(selected):
        h = kernel.OpenProcess(0x0410, False, pid)  # QUERY_INFORMATION|VM_READ
        if not h:
            raise OSError(f"Unable to sample Holding descendant PID {pid}")
        try:
            stats = Counters()
            stats.cb = ctypes.sizeof(stats)
            if not psapi.GetProcessMemoryInfo(
                    h, ctypes.byref(stats), stats.cb):
                raise OSError(f"Unable to inspect Holding descendant PID {pid}")
            measured.append(ProcessMemory(
                pid, metadata[pid].parent_pid, metadata[pid].name,
                int(stats.WorkingSetSize)))
        finally:
            kernel.CloseHandle(h)
    return account_tree(tuple(measured), roots)


class MemorySampler:
    """Short-window sampling of dynamic engine+viewer roots.

    Never treat this as a production RAM certificate. No sample/no root is FAIL.
    """
    def __init__(self, *, interval: float = 0.1,
                 sample: Callable[[set[int]], MemorySample] = windows_tree_sample):
        if not 0.02 <= interval <= 2.0:
            raise ValueError("Sampling interval outside 20ms..2s")
        self.interval = interval
        self._sample = sample
        self._roots: set[int] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None
        self.samples = 0
        self.peak_holding_bytes = 0
        self.peak_model_bytes = 0
        self.seen_roots: set[int] = set()

    def add_root(self, pid: int) -> None:
        if type(pid) is not int or pid <= 0:
            raise ValueError("Invalid process PID")
        with self._lock:
            self._roots.add(pid)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Memory sampler already started")
        self._thread = threading.Thread(target=self._loop, name="holding-ram-smoke",
                                        daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                roots = set(self._roots)
            if roots:
                try:
                    record = self._sample(roots)
                    self.samples += 1
                    self.peak_holding_bytes = max(
                        self.peak_holding_bytes, record.holding_bytes)
                    self.peak_model_bytes = max(
                        self.peak_model_bytes, record.model_bytes)
                    self.seen_roots.update(set(record.included_pids)
                                           & roots)
                except Exception as exc:
                    self._error = exc
                    break
            self._stop.wait(self.interval)

    def finish(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Memory sampler did not terminate")
        if self._error is not None:
            raise RuntimeError("Process-tree RAM sample failed") from self._error
        if self.samples < 1 or not self._roots.issubset(self.seen_roots):
            raise RuntimeError("Missing required engine/viewer RAM sample")
        return {
            "kind": "NONRELEASE_FROZEN_IPC_RAM_WINDOW",
            "samples": self.samples,
            "root_pids_observed": len(self.seen_roots),
            "peak_holding_working_set_bytes": self.peak_holding_bytes,
            "peak_model_working_set_bytes": self.peak_model_bytes,
            "sample_interval_ms": round(self.interval * 1000),
            "normal_coding_certified": False,
            "release_ready": False,
        }

"""Fail-closed preflight for free disk and physical RAM before new agent runs.

Does not watch games or run expensive benchmarks. CPU/GPU throttling and OS
job-object limits are separately required before production certification.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys


@dataclass(frozen=True)
class ResourceReport:
    safe: bool
    reason: str
    free_disk: int | None
    free_ram: int | None


def available_ram() -> int | None:
    if sys.platform == 'win32':
        # Windows MEMORYSTATUSEX
        try:
            import ctypes
            from ctypes import wintypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD),
                            ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                            ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                            ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return int(status.ullAvailPhys)
        except (AttributeError, OSError, ValueError):
            return None
    try:
        with open('/proc/meminfo', encoding='ascii') as file:
            for line in file:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


@dataclass(frozen=True)
class ResourceGuard:
    disk_path: Path
    min_free_disk: int = 4 * 1024 ** 3
    min_free_ram: int = 2 * 1024 ** 3

    def __post_init__(self):
        if self.min_free_disk < 0 or self.min_free_ram < 0:
            raise ValueError('Resource thresholds must not be negative')
        if not self.disk_path.is_absolute():
            raise ValueError('Disk resource check needs an absolute location')

    def check(self) -> ResourceReport:
        try:
            free_disk = shutil.disk_usage(self.disk_path).free
        except (OSError, ValueError):
            return ResourceReport(False, 'DISK_UNKNOWN', None, None)
        if free_disk < self.min_free_disk:
            return ResourceReport(False, 'DISK_PRESSURE', free_disk, None)
        free_ram = available_ram()
        if free_ram is None:
            return ResourceReport(False, 'RAM_UNKNOWN', free_disk, None)
        if free_ram < self.min_free_ram:
            return ResourceReport(False, 'RAM_PRESSURE', free_disk, free_ram)
        return ResourceReport(True, 'OK', free_disk, free_ram)

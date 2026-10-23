"""Native Windows user-session gate before launching the installed GUI engine.

A headless service in Session 0 must not report the GUI as launched. This
check is independent of pinned executable hashes and ACL validation.
"""
from __future__ import annotations

import os


class InteractiveSessionError(RuntimeError):
    pass


def require_interactive_windows_session() -> None:
    """Reject Session 0, unknown sessions, and nonvisible window stations."""
    if os.name != "nt":
        raise InteractiveSessionError("Interactive Windows user session required")

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)

    kernel.GetCurrentProcessId.argtypes = ()
    kernel.GetCurrentProcessId.restype = wintypes.DWORD
    kernel.ProcessIdToSessionId.argtypes = (
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    user.GetProcessWindowStation.argtypes = ()
    user.GetProcessWindowStation.restype = wintypes.HANDLE
    user.GetUserObjectInformationW.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    user.GetUserObjectInformationW.restype = wintypes.BOOL

    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(
            kernel.GetCurrentProcessId(), ctypes.byref(session)):
        raise InteractiveSessionError("Windows session identity unavailable")
    if session.value == 0:
        raise InteractiveSessionError("Windows service Session 0 is not interactive")

    class _UserObjectFlags(ctypes.Structure):
        _fields_ = (
            ("fInherit", wintypes.BOOL),
            ("fReserved", wintypes.BOOL),
            ("dwFlags", wintypes.DWORD),
        )

    window_station = user.GetProcessWindowStation()
    if not window_station:
        raise InteractiveSessionError("Windows process window station unavailable")
    flags = _UserObjectFlags()
    size = wintypes.DWORD()
    if not user.GetUserObjectInformationW(
            window_station, 1, ctypes.byref(flags),
            ctypes.sizeof(flags), ctypes.byref(size)):
        raise InteractiveSessionError("Windows window station rights unavailable")
    if not flags.dwFlags & 0x0001:  # WSF_VISIBLE
        raise InteractiveSessionError("Windows window station is not interactive")

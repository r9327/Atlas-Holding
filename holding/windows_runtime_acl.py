"""Win32-only fail-closed runtime DACL hardening and verification.

The installed GUI and daemon are executable code, not credential files.
Restrict write access to the installing user's SID, SYSTEM and Administrators.
Each ACL is applied and inspected on Windows; Linux tests remain explicitly
non-authoritative and NEVER qualify as Windows security evidence.
"""
from __future__ import annotations

import os
from pathlib import Path


class RuntimeAclError(ValueError):
    pass


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _FILE_OBJECT = 1
    _OWNER = 0x1
    _DACL = 0x4
    _PROTECTED_DACL = 0x80000000
    _DACL_PROTECTED = 0x1000
    _TOKEN_QUERY = 0x8
    _TOKEN_USER = 1
    _INSUFFICIENT_BUFFER = 122
    _ACL_SIZE_INFORMATION = 2
    _ALLOW_ACE = 0
    _FULL_ACCESS = 0x001F01FF
    _ADMIN_SID = "S-1-5-32-544"
    _SYSTEM_SID = "S-1-5-18"

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p),
                    ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER_STRUCT(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _ACL_SIZE(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", wintypes.BYTE),
            ("AceFlags", wintypes.BYTE),
            ("AceSize", wintypes.WORD),
        ]

    _advapi.OpenProcessToken.argtypes = (
        wintypes.HANDLE, wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _advapi.OpenProcessToken.restype = wintypes.BOOL
    _advapi.GetTokenInformation.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    )
    _advapi.GetTokenInformation.restype = wintypes.BOOL
    _advapi.IsValidSid.argtypes = (ctypes.c_void_p,)
    _advapi.IsValidSid.restype = wintypes.BOOL
    _advapi.ConvertSidToStringSidW.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR),
    )
    _advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi.ConvertStringSidToSidW.argtypes = (
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi.CheckTokenMembership.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
    )
    _advapi.CheckTokenMembership.restype = wintypes.BOOL
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _advapi.SetFileSecurityW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
    )
    _advapi.SetFileSecurityW.restype = wintypes.BOOL
    _advapi.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi.GetSecurityDescriptorControl.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi.GetAclInformation.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD,
    )
    _advapi.GetAclInformation.restype = wintypes.BOOL
    _advapi.GetAce.argtypes = (
        ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi.GetAce.restype = wintypes.BOOL
    _kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel.CloseHandle.restype = wintypes.BOOL
    _kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    _kernel.LocalFree.restype = ctypes.c_void_p


def _sid_text(pointer: object) -> str:
    if not _advapi.IsValidSid(pointer):
        raise RuntimeAclError("Invalid Windows security identity")
    buffer = wintypes.LPWSTR()
    if not _advapi.ConvertSidToStringSidW(pointer, ctypes.byref(buffer)):
        raise RuntimeAclError("Cannot resolve Windows security identity")
    try:
        return buffer.value.upper()
    finally:
        _kernel.LocalFree(ctypes.cast(buffer, ctypes.c_void_p))


def _current_sid() -> str:
    handle = wintypes.HANDLE()
    if not _advapi.OpenProcessToken(
            _kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(handle)):
        raise RuntimeAclError("Cannot read Windows process identity")
    try:
        size = wintypes.DWORD()
        _advapi.GetTokenInformation(
            handle, _TOKEN_USER, None, 0, ctypes.byref(size))
        if (ctypes.get_last_error() != _INSUFFICIENT_BUFFER
                or not size.value or size.value > 65536):
            raise RuntimeAclError("Invalid Windows token identity length")
        block = ctypes.create_string_buffer(size.value)
        if not _advapi.GetTokenInformation(
                handle, _TOKEN_USER, block, size, ctypes.byref(size)):
            raise RuntimeAclError("Cannot inspect Windows account SID")
        return _sid_text(ctypes.cast(
            block, ctypes.POINTER(_TOKEN_USER_STRUCT)).contents.User.Sid)
    finally:
        _kernel.CloseHandle(handle)


def _is_elevated_administrator() -> bool:
    """Check actual enabled group membership, not a name or claim."""
    sid = ctypes.c_void_p()
    if not _advapi.ConvertStringSidToSidW(
            _ADMIN_SID, ctypes.byref(sid)):
        raise RuntimeAclError("Cannot resolve native Administrators SID")
    try:
        allowed = wintypes.BOOL()
        if not _advapi.CheckTokenMembership(
                None, sid, ctypes.byref(allowed)):
            raise RuntimeAclError("Cannot verify elevated Windows membership")
        return bool(allowed.value)
    finally:
        if sid.value:
            _kernel.LocalFree(sid)


def _validate_target(path: Path) -> None:
    if (not isinstance(path, Path) or not path.is_absolute()
            or ".." in path.parts or path.is_symlink()
            or not (path.is_dir() or path.is_file())):
        raise RuntimeAclError("Unsafe runtime ACL target")
    try:
        if path.resolve(strict=True) != path:
            raise RuntimeAclError("Runtime target must use canonical path")
    except OSError as exc:
        raise RuntimeAclError("Runtime target unavailable") from exc


def validate_runtime_acl(path: Path) -> None:
    """Verify native owner, protected DACL, and every allowed Windows SID."""
    if os.name != "nt":
        raise RuntimeAclError("Native Windows ACLs unavailable")
    _validate_target(path)
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = _advapi.GetNamedSecurityInfoW(
        str(path), _FILE_OBJECT, _OWNER | _DACL,
        ctypes.byref(owner), None, ctypes.byref(dacl), None,
        ctypes.byref(descriptor),
    )
    if result:
        raise RuntimeAclError("Cannot read runtime Windows DACL")
    try:
        account = _current_sid()
        if not owner.value:
            raise RuntimeAclError("Runtime owner unavailable")
        actual_owner = _sid_text(owner)
        # Elevated Windows processes may create objects owned by the local
        # Administrators group. Accept this ONLY when membership is enabled
        # in the effective token (never a UAC deny-only group). Ordinary users
        # must own their objects under their exact account SID.
        if (actual_owner != account
                and not (actual_owner == _ADMIN_SID
                         and _is_elevated_administrator())):
            raise RuntimeAclError("Runtime owner is not installer identity")
        if not dacl.value:
            raise RuntimeAclError("Runtime DACL cannot be null")
        flags = wintypes.WORD()
        revision = wintypes.DWORD()
        if not _advapi.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(flags), ctypes.byref(revision)):
            raise RuntimeAclError("Cannot inspect runtime DACL controls")
        if not flags.value & _DACL_PROTECTED:
            raise RuntimeAclError("Runtime permissions inherit untrusted ACEs")
        acl_size = _ACL_SIZE()
        if not _advapi.GetAclInformation(
                dacl, ctypes.byref(acl_size), ctypes.sizeof(acl_size),
                _ACL_SIZE_INFORMATION):
            raise RuntimeAclError("Cannot inspect runtime ACE count")
        if not 1 <= acl_size.AceCount <= 3:
            raise RuntimeAclError("Unexpected runtime ACE count")
        allowed = {account, _SYSTEM_SID, _ADMIN_SID}
        seen = set()
        for index in range(acl_size.AceCount):
            ace = ctypes.c_void_p()
            if not _advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise RuntimeAclError("Cannot read runtime ACE")
            hdr = ctypes.cast(ace, ctypes.POINTER(_ACE_HEADER)).contents
            if hdr.AceType != _ALLOW_ACE or hdr.AceSize < 16:
                raise RuntimeAclError("Unsupported runtime permission entry")
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            sid = _sid_text(ctypes.c_void_p(ace.value + 8))
            if (sid not in allowed or sid in seen
                    or mask != _FULL_ACCESS or hdr.AceFlags & 0x10):
                raise RuntimeAclError("Runtime ACL grants unsafe permissions")
            seen.add(sid)
        if seen != allowed:
            raise RuntimeAclError("Runtime lacks required account/SYSTEM/Admin ACEs")
    finally:
        if descriptor.value:
            _kernel.LocalFree(descriptor)


def secure_runtime_acl(path: Path) -> None:
    """Apply and recheck strict native per-user runtime ACL (fail closed)."""
    if os.name != "nt":
        raise RuntimeAclError("Windows native permission enforcement required")
    _validate_target(path)
    sid = _current_sid()
    inherited = "OICI" if path.is_dir() else ""
    sddl = ("D:P" + "".join(
        f"(A;{inherited};FA;;;{identity})"
        for identity in (sid, _SYSTEM_SID, _ADMIN_SID)
    ))
    descriptor = ctypes.c_void_p()
    size = wintypes.DWORD()
    if not _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), ctypes.byref(size)):
        raise RuntimeAclError("Cannot construct protected runtime ACL")
    try:
        if not _advapi.SetFileSecurityW(
                str(path), _DACL | _PROTECTED_DACL, descriptor):
            raise RuntimeAclError("Cannot enforce native Windows runtime ACL")
    finally:
        if descriptor.value:
            _kernel.LocalFree(descriptor)
    validate_runtime_acl(path)

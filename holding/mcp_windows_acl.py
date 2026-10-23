"""Native Windows ACL gate for trusted MCP bootstrap inputs.

Reject owners outside current user/SYSTEM/Administrators and any DACL entry
granting write-like rights to an untrusted SID. Deny unknown ACE formats.
This is an intentionally restrictive check, not an ACL editor or a sandbox.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import stat


class WindowsAclError(ValueError):
    pass


_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_ACL_ACCESS_ALLOWED = 0
_ACL_ACCESS_DENIED = 1
_UNSAFE_WRITE_MASK = (
    0x10000000  # GENERIC_ALL
    | 0x40000000  # GENERIC_WRITE
    | 0x00040000  # WRITE_DAC
    | 0x00080000  # WRITE_OWNER
    | 0x00010000  # DELETE
    | 0x00000002  # FILE_WRITE_DATA
    | 0x00000004  # FILE_APPEND_DATA
    | 0x00000010  # FILE_WRITE_EA
    | 0x00000040  # FILE_DELETE_CHILD
    | 0x00000100  # FILE_WRITE_ATTRIBUTES
)


class _AclHeader(ctypes.Structure):
    _fields_ = [("revision", ctypes.c_ubyte),
                ("reserved1", ctypes.c_ubyte),
                ("size", ctypes.c_uint16),
                ("ace_count", ctypes.c_uint16),
                ("reserved2", ctypes.c_uint16)]


class _AceHeader(ctypes.Structure):
    _fields_ = [("ace_type", ctypes.c_ubyte),
                ("ace_flags", ctypes.c_ubyte),
                ("ace_size", ctypes.c_uint16)]


def _api():
    if os.name != "nt":
        raise WindowsAclError("Native Windows ACL check requires Windows")
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.GetAce.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    adv.GetAce.restype = wintypes.BOOL
    adv.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    adv.EqualSid.restype = wintypes.BOOL
    adv.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertStringSidToSidW.restype = wintypes.BOOL
    adv.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    adv.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.OpenProcessToken.restype = wintypes.BOOL
    adv.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    adv.GetTokenInformation.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return adv, kernel


def current_windows_user_sid() -> str:
    """Current native account SID, for controlled test provisioning only."""
    adv, kernel = _api()
    handle = wintypes.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), _TOKEN_QUERY,
                                ctypes.byref(handle)):
        raise WindowsAclError("Windows identity cannot be read")
    try:
        length = wintypes.DWORD(0)
        adv.GetTokenInformation(handle, _TOKEN_USER, None, 0,
                                ctypes.byref(length))
        if not 16 <= length.value <= 65536:
            raise WindowsAclError("Invalid Windows token identity")
        buf = ctypes.create_string_buffer(length.value)
        if not adv.GetTokenInformation(handle, _TOKEN_USER, buf, length,
                                       ctypes.byref(length)):
            raise WindowsAclError("Windows identity cannot be verified")
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        output = ctypes.c_void_p()
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(output)):
            raise WindowsAclError("Windows identity SID unavailable")
        try:
            return ctypes.wstring_at(output.value)
        finally:
            kernel.LocalFree(output)
    finally:
        kernel.CloseHandle(handle)


def _enforce_one(path: Path, adv, kernel, allowed: list[ctypes.c_void_p]) -> None:
    prior = path.lstat()
    if (not (stat.S_ISREG(prior.st_mode) or stat.S_ISDIR(prior.st_mode))
            or getattr(prior, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise WindowsAclError("Trusted input must not be a reparse point")
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    code = adv.GetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner), None, ctypes.byref(dacl), None,
        ctypes.byref(descriptor))
    if code:
        raise WindowsAclError("Windows file security cannot be inspected")
    try:
        if not owner.value or not dacl.value:
            raise WindowsAclError("Windows input lacks a private DACL")
        if not any(adv.EqualSid(owner, sid) for sid in allowed):
            raise WindowsAclError("Windows input owner is untrusted")
        acl = ctypes.cast(dacl, ctypes.POINTER(_AclHeader)).contents
        if acl.size < 8 or acl.ace_count > 128:
            raise WindowsAclError("Windows input ACL is not bounded")
        for index in range(acl.ace_count):
            ace = ctypes.c_void_p()
            if not adv.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
                raise WindowsAclError("Windows ACL entry is unreadable")
            hdr = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
            if hdr.ace_size < 8 or hdr.ace_type not in {
                    _ACL_ACCESS_ALLOWED, _ACL_ACCESS_DENIED}:
                raise WindowsAclError("Windows ACL contains unsupported entries")
            if hdr.ace_type == _ACL_ACCESS_ALLOWED:
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                sid = ctypes.c_void_p(ace.value + 8)
                if (mask & _UNSAFE_WRITE_MASK
                        and not any(adv.EqualSid(sid, principal)
                                    for principal in allowed)):
                    raise WindowsAclError("Windows ACL grants untrusted write access")
    finally:
        kernel.LocalFree(descriptor)


def require_private_windows_path(path: Path, *, check_parent: bool = True) -> None:
    """Check both the trusted file and the directory that can replace it."""
    if os.name != "nt":
        raise WindowsAclError("Native Windows ACL check requires Windows")
    if not isinstance(path, Path) or not path.is_absolute():
        raise WindowsAclError("Trusted absolute Windows path required")
    adv, kernel = _api()
    sid_strings = [current_windows_user_sid(), "S-1-5-18", "S-1-5-32-544"]
    allowed = []
    try:
        for name in sid_strings:
            sid = ctypes.c_void_p()
            if not adv.ConvertStringSidToSidW(name, ctypes.byref(sid)):
                raise WindowsAclError("Trusted Windows SID cannot be resolved")
            allowed.append(sid)
        _enforce_one(path, adv, kernel, allowed)
        if check_parent:
            _enforce_one(path.parent, adv, kernel, allowed)
    except OSError as exc:
        raise WindowsAclError("Windows private input unavailable") from exc
    finally:
        for sid in allowed:
            kernel.LocalFree(sid)

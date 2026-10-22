"""Fail-closed Windows ACL validation for an already-open secret file.

The validation is performed on the same file handle that the caller reads, so
replacing a pathname after validation cannot redirect the credential read.
"""
from __future__ import annotations

import os


class WindowsSecretAclError(RuntimeError):
    """The secret handle does not satisfy the privileged-worker ACL policy."""


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _DACL_SECURITY_INFORMATION = 0x00000004
    _SE_DACL_PROTECTED = 0x1000
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _ACL_SIZE_INFORMATION_CLASS = 2
    _ACCESS_ALLOWED_ACE_TYPE = 0
    _ACCESS_DENIED_ACE_TYPE = 1
    _INHERITED_ACE = 0x10
    _ERROR_INSUFFICIENT_BUFFER = 122
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    _ALLOWED_SERVICE_SIDS = {"S-1-5-18", "S-1-5-32-544"}

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER_VALUE(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
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

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    _advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetSecurityInfo.restype = wintypes.DWORD
    _advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    _advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
    ]
    _advapi32.GetAclInformation.restype = wintypes.BOOL
    _advapi32.GetAce.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetAce.restype = wintypes.BOOL
    _advapi32.IsValidSid.argtypes = [ctypes.c_void_p]
    _advapi32.IsValidSid.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR),
    ]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ]
    _kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _native_failure(operation: str) -> WindowsSecretAclError:
    """Return a stable error without including paths, SIDs, or token data."""
    return WindowsSecretAclError(f"Windows ACL validation failed: {operation}")


def _sid_string(sid: object) -> str:
    if not _advapi32.IsValidSid(sid):
        raise _native_failure("invalid SID")
    text = wintypes.LPWSTR()
    if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise _native_failure("SID conversion")
    try:
        return text.value.upper()
    finally:
        _kernel32.LocalFree(text)


def current_process_sid() -> str:
    """Return the SID of the process token used by the privileged worker."""
    if os.name != "nt":
        raise WindowsSecretAclError("Windows ACL validation is unavailable")
    token = wintypes.HANDLE()
    if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _native_failure("process token")
    try:
        size = wintypes.DWORD()
        _advapi32.GetTokenInformation(
            token, _TOKEN_USER, None, 0, ctypes.byref(size))
        if (ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER
                or not size.value):
            raise _native_failure("token size")
        buffer = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(
                token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
            raise _native_failure("token identity")
        token_user = ctypes.cast(
            buffer, ctypes.POINTER(_TOKEN_USER_VALUE)).contents
        return _sid_string(token_user.User.Sid)
    finally:
        _kernel32.CloseHandle(token)


def validate_secret_file_handle(handle: int) -> None:
    """Validate owner, inheritance, and allow ACEs on a Windows file handle."""
    if os.name != "nt":
        raise WindowsSecretAclError("Windows ACL validation is unavailable")
    attributes = _FILE_ATTRIBUTE_TAG_INFO()
    if not _kernel32.GetFileInformationByHandleEx(
            wintypes.HANDLE(handle), _FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(attributes), ctypes.sizeof(attributes)):
        raise _native_failure("file attributes")
    if attributes.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsSecretAclError("Token file must not be a reparse point")
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = _advapi32.GetSecurityInfo(
        wintypes.HANDLE(handle), _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner), None, ctypes.byref(dacl), None,
        ctypes.byref(descriptor),
    )
    if result:
        raise _native_failure("security descriptor")
    try:
        if not owner.value or not dacl.value:
            raise WindowsSecretAclError("Token file requires an owner and DACL")
        process_sid = current_process_sid()
        if _sid_string(owner) != process_sid:
            raise WindowsSecretAclError("Token file owner is not the worker identity")

        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not _advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise _native_failure("descriptor control")
        if not control.value & _SE_DACL_PROTECTED:
            raise WindowsSecretAclError("Token file DACL must disable inheritance")

        info = _ACL_SIZE_INFORMATION()
        if not _advapi32.GetAclInformation(
                dacl, ctypes.byref(info), ctypes.sizeof(info),
                _ACL_SIZE_INFORMATION_CLASS):
            raise _native_failure("DACL information")
        allowed_sids = _ALLOWED_SERVICE_SIDS | {process_sid}
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            if not _advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise _native_failure("DACL entry")
            header = ctypes.cast(ace, ctypes.POINTER(_ACE_HEADER)).contents
            if header.AceFlags & _INHERITED_ACE:
                raise WindowsSecretAclError("Token file has an inherited DACL entry")
            if header.AceType not in (
                    _ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE):
                raise WindowsSecretAclError("Token file has an unsupported DACL entry")
            if header.AceSize < ctypes.sizeof(_ACE_HEADER) + 4 + 8:
                raise WindowsSecretAclError("Token file has an invalid DACL entry")
            if header.AceType == _ACCESS_ALLOWED_ACE_TYPE:
                sid_address = ace.value + ctypes.sizeof(_ACE_HEADER) + 4
                if _sid_string(ctypes.c_void_p(sid_address)) not in allowed_sids:
                    raise WindowsSecretAclError(
                        "Token file grants access to an untrusted identity")
    finally:
        if descriptor.value:
            _kernel32.LocalFree(descriptor)

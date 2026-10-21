"""Injected native Windows station results: Session 0 is never GUI-ready."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from holding.windows_user_session import (
    InteractiveSessionError, require_interactive_windows_session,
)


class WindowsUserSessionTests(unittest.TestCase):
    def call_with_native_response(
        self, *, session=3, visible=True, query_ok=True, station_ok=True,
    ):
        kernel = MagicMock()
        user = MagicMock()
        kernel.GetCurrentProcessId.return_value = 1234

        def session_lookup(pid, pointer):
            if not query_ok:
                return False
            ctypes.cast(
                pointer, ctypes.POINTER(wintypes.DWORD),
            ).contents.value = session
            return True

        def station_info(station_handle, info_kind, flags, size, required):
            self.assertEqual(info_kind, 1)
            if not station_ok:
                return False
            ctypes.cast(
                flags, ctypes.POINTER(ctypes.c_uint32),
            )[2] = 1 if visible else 0
            return True

        kernel.ProcessIdToSessionId.side_effect = session_lookup
        user.GetProcessWindowStation.return_value = 987
        user.GetUserObjectInformationW.side_effect = station_info

        def win_dll(name, **_):
            return kernel if name == "kernel32" else user

        with patch("holding.windows_user_session.os", SimpleNamespace(name="nt")), \
             patch.object(ctypes, "WinDLL", win_dll, create=True):
            require_interactive_windows_session()
        return kernel, user

    def test_rejects_nonwindows_platform(self):
        with patch("holding.windows_user_session.os", SimpleNamespace(name="posix")):
            with self.assertRaisesRegex(
                InteractiveSessionError, "Windows user session"):
                require_interactive_windows_session()

    def test_denies_session_zero_even_with_visible_window_station(self):
        with self.assertRaisesRegex(InteractiveSessionError, "Session 0"):
            self.call_with_native_response(session=0)

    def test_denies_invisible_window_station_in_logged_in_session(self):
        with self.assertRaisesRegex(InteractiveSessionError, "not interactive"):
            self.call_with_native_response(session=3, visible=False)

    def test_denies_unknown_process_session(self):
        with self.assertRaisesRegex(InteractiveSessionError, "identity"):
            self.call_with_native_response(query_ok=False)

    def test_denies_unknown_window_station_flags(self):
        with self.assertRaisesRegex(InteractiveSessionError, "rights"):
            self.call_with_native_response(station_ok=False)

    def test_accepts_only_positive_session_with_visible_station(self):
        kernel, user = self.call_with_native_response(session=3, visible=True)
        kernel.ProcessIdToSessionId.assert_called_once()
        user.GetProcessWindowStation.assert_called_once()
        user.GetUserObjectInformationW.assert_called_once()


if __name__ == "__main__":
    unittest.main()

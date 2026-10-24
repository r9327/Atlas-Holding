"""Exact SHA-pinned managed executable bindings do not require global PATH."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.managed_toolchain import ManagedTool, resolve_managed_tool
from holding.native_health import NativeHealth
from holding.staged_install import InstallInspection, PinnedZip


class ManagedToolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.pin = PinnedZip(
            "git", "2.57.0", "https://example.invalid/pinned.zip",
            "a" * 64, 1024, 2048, "cmd/git.exe", "2.57.0",
        )
        self.target = self.root / "components" / "git" / "pinned"

    def test_pinned_executable_is_returned_by_absolute_path_only_after_probes(self):
        with patch("holding.managed_toolchain.check_staged_binary",
                   return_value=NativeHealth("git", "BINARY_VERSION_PROBED")):
            with patch("holding.managed_toolchain.inspect_versioned_zip",
                       return_value=InstallInspection("VERIFIED_PAYLOAD",
                                                      self.target)):
                tool = resolve_managed_tool(self.pin, install_root=self.root)
        self.assertEqual(tool, ManagedTool(
            "git", "VERIFIED_EXECUTABLE",
            str(self.target / "cmd" / "git.exe"), "2.57.0",
        ))
        self.assertTrue(Path(tool.executable).is_absolute())

    def test_no_native_pin_never_returns_runnable_path(self):
        no_probe = PinnedZip("git", "2.57.0", self.pin.url, self.pin.sha256,
                             1024, 2048)
        with patch("holding.managed_toolchain.check_staged_binary") as probe:
            tool = resolve_managed_tool(no_probe, install_root=self.root)
        self.assertEqual(tool.state, "NO_NATIVE_PROBE_CONFIGURED")
        self.assertEqual(tool.executable, "")
        probe.assert_not_called()

    def test_failed_native_version_or_modified_files_deny_binding(self):
        for state in ("BLOCKED_INVALID", "BINARY_VERSION_MISMATCH",
                      "BINARY_PROBE_FAILED", "NOT_INSTALLED"):
            with self.subTest(state=state):
                with patch("holding.managed_toolchain.check_staged_binary",
                           return_value=NativeHealth("git", state)):
                    with patch("holding.managed_toolchain.inspect_versioned_zip") as inspect:
                        tool = resolve_managed_tool(self.pin, install_root=self.root)
                self.assertEqual(tool.state, state)
                self.assertEqual(tool.executable, "")
                inspect.assert_not_called()
        with patch("holding.managed_toolchain.check_staged_binary",
                   return_value=NativeHealth("git", "BINARY_VERSION_PROBED")):
            with patch("holding.managed_toolchain.inspect_versioned_zip",
                       return_value=InstallInspection("BLOCKED_INVALID",
                                                      self.target)):
                tool = resolve_managed_tool(self.pin, install_root=self.root)
        self.assertEqual(tool.state, "CHANGED_AFTER_NATIVE_PROBE")
        self.assertEqual(tool.executable, "")

    def test_untrusted_component_is_not_promoted(self):
        other = PinnedZip("untrusted", "2.57.0", self.pin.url,
                          self.pin.sha256, 1024, 2048, "cmd/x.exe", "2.57.0")
        with patch("holding.managed_toolchain.check_staged_binary") as probe:
            tool = resolve_managed_tool(other, install_root=self.root)
        self.assertEqual(tool.state, "UNSUPPORTED_COMPONENT")
        probe.assert_not_called()

    def test_relative_install_root_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_managed_tool(self.pin, install_root=Path("relative"))


if __name__ == "__main__":
    unittest.main()

"""Trusted, installed CPython companion identity independent of Engine.exe."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from holding.mcp_companion_trust import (
    McpCompanionTrustError, verify_internal_hub_runtime)
from holding.mcp_local_inventory import PinnedLocalPlugin
from holding.mcp_package_pin import McpPackagePin
from holding.mcp_hub_session import LocalMcpHubSession

NAMES = (
    "__init__.py", "mcp_entry.py", "mcp_stdio.py",
    "mcp_hub_session.py", "mcp_hub_probe.py",
    "mcp_windows_job.py", "mcp_local_inventory.py",
)


class InstalledCompanionIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        archive_digest = "a" * 64
        self.installed = (
            root / "Atlas Holding" / "mcp-python" /
            ("python-embedded-" + archive_digest[:16]))
        package = self.installed / "holding"
        package.mkdir(parents=True)
        self.exe = self.installed / "python.exe"
        self.exe.write_bytes(b"fake CPython for identity-only testing")
        modules = {}
        for name in NAMES:
            payload = ("# " + name + "\n").encode()
            (package / name).write_bytes(payload)
            modules[name] = hashlib.sha256(payload).hexdigest()
        self.pin = PinnedLocalPlugin(
            "atlas-internal-python", "3.13", self.exe,
            hashlib.sha256(self.exe.read_bytes()).hexdigest())
        self.package_pin = McpPackagePin.from_manifest(modules)

    def test_exact_package_and_interpreter_are_allowed_without_sys_executable(self):
        self.assertEqual(
            verify_internal_hub_runtime(
                interpreter_pin=self.pin, package_root=self.installed,
                package_pin=self.package_pin),
            "INSTALLED_PINNED_COMPANION")
        session = LocalMcpHubSession(
            interpreter_pin=self.pin, package_root=self.installed,
            package_pin=self.package_pin)
        self.assertEqual(session.observe().state, "NOT_STARTED")
        session.close()

    def test_companion_without_module_pins_is_never_trusted(self):
        with self.assertRaises(McpCompanionTrustError):
            verify_internal_hub_runtime(
                interpreter_pin=self.pin, package_root=self.installed,
                package_pin=None)

    def test_invalid_name_or_modified_source_cannot_claim_first_party_companion(self):
        (self.installed / "holding" / "mcp_entry.py").write_text(
            "# attacker edits pinned module\n")
        with self.assertRaises(McpCompanionTrustError):
            verify_internal_hub_runtime(
                interpreter_pin=self.pin, package_root=self.installed,
                package_pin=self.package_pin)
        with self.assertRaises(ValueError):
            LocalMcpHubSession(
                interpreter_pin=self.pin, package_root=self.installed,
                package_pin=self.package_pin)

    def test_modified_python_binary_or_wrong_install_dir_is_rejected(self):
        self.exe.write_bytes(b"changed interpreter")
        with self.assertRaises(McpCompanionTrustError):
            verify_internal_hub_runtime(
                interpreter_pin=self.pin, package_root=self.installed,
                package_pin=self.package_pin)
        self.exe.write_bytes(b"fake CPython for identity-only testing")
        alias = self.installed.parent.parent / "arbitrary-runtime"
        self.installed.rename(alias)
        with self.assertRaises(McpCompanionTrustError):
            verify_internal_hub_runtime(
                interpreter_pin=self.pin, package_root=alias,
                package_pin=self.package_pin)


if __name__ == "__main__":
    unittest.main()

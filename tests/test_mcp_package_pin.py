"""Pinned local package integrity across trusted MCP process launches."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.mcp_package_pin import (
    McpPackagePin, McpPackagePinError,
    build_installation_module_pins,
    MAX_PY_FILES,
)


REQUIRED = (
    "__init__.py", "mcp_entry.py", "mcp_stdio.py",
    "mcp_hub_session.py", "mcp_hub_probe.py",
    "mcp_windows_job.py", "mcp_local_inventory.py",
)


class McpPackagePinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / "holding"
        self.package.mkdir()
        for name in REQUIRED:
            (self.package / name).write_text("INITIAL = 1\n", encoding="utf-8")

    def test_exact_manifest_verifies_files_and_rejects_modified_byte(self):
        pin = build_installation_module_pins(self.root)
        self.assertIsInstance(pin, McpPackagePin)
        self.assertTrue(pin.verify(self.root))
        (self.package / "mcp_entry.py").write_text(
            "INITIAL = 2\n", encoding="utf-8")
        self.assertFalse(pin.verify(self.root))
        (self.package / "mcp_entry.py").write_text(
            "INITIAL = 1\n", encoding="utf-8")
        self.assertTrue(pin.verify(self.root))

    def test_extra_new_module_and_deleted_import_fail_closed(self):
        pin = build_installation_module_pins(self.root)
        extra = self.package / "injected.py"
        extra.write_text("PWN = True\n", encoding="utf-8")
        self.assertFalse(pin.verify(self.root))
        extra.unlink()
        (self.package / "mcp_hub_probe.py").unlink()
        with self.assertRaises(McpPackagePinError):
            pin.verify(self.root)

    def test_unpinned_native_extension_or_bytecode_cannot_shadow_source(self):
        pin = build_installation_module_pins(self.root)
        for suffix in (".pyd", ".so", ".dll", ".pyc"):
            shadow = self.package / ("mcp_entry" + suffix)
            with self.subTest(suffix=suffix):
                shadow.write_bytes(b"hostile module bytes")
                with self.assertRaises(McpPackagePinError):
                    pin.verify(self.root)
                shadow.unlink()
                self.assertTrue(pin.verify(self.root))

    def test_symlink_and_large_source_are_refused(self):
        link = self.package / "newimport.py"
        try:
            link.symlink_to(self.package / "mcp_entry.py")
        except (NotImplementedError, OSError):
            self.skipTest("Windows account cannot create symlinks")
        with self.assertRaises(McpPackagePinError):
            build_installation_module_pins(self.root)
        link.unlink()
        (self.package / "mcp_entry.py").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaises(McpPackagePinError):
            build_installation_module_pins(self.root)

    def test_duplicate_unordered_missing_and_malformed_pins_are_rejected(self):
        pin = build_installation_module_pins(self.root)
        items = list(pin.entries)
        for entries in (
            tuple(reversed(items)),
            tuple(items + items[:1]),
            tuple(items[:-1] + [("zz-extra.py", "a" * 64)]),
            tuple(items[:-1] + [("evil.py", "0" * 40)]),
            tuple(items[:-1] + [("../escape.py", "a" * 64)]),
        ):
            with self.subTest(entries=len(entries)):
                if entries[-1][0] == "zz-extra.py":
                    # Well-formed but not an exact inventory.
                    self.assertFalse(McpPackagePin(entries).verify(self.root))
                else:
                    with self.assertRaises(McpPackagePinError):
                        McpPackagePin(entries)
        with self.assertRaises(McpPackagePinError):
            McpPackagePin.from_manifest(["wrong"])
        with self.assertRaises(McpPackagePinError):
            McpPackagePin(tuple(items * (MAX_PY_FILES // len(items) + 1)))

    def test_manifest_never_accepts_arbitrary_package_root(self):
        with self.assertRaises(McpPackagePinError):
            build_installation_module_pins(Path("."))
        with self.assertRaises(McpPackagePinError):
            build_installation_module_pins(self.root / "missing")

    def test_code_digest_change_after_launch_invalidates_protocol_evidence(self):
        from holding.mcp_hub_session import LocalMcpHubSession
        from holding.mcp_local_inventory import PinnedLocalPlugin
        import hashlib
        import sys
        root = Path(__file__).resolve().parents[1]
        executable = Path(sys.executable).resolve()
        interpreter = PinnedLocalPlugin(
            "hub-python", "3.13", executable,
            hashlib.sha256(executable.read_bytes()).hexdigest())
        pin = build_installation_module_pins(root)
        with patch("holding.mcp_package_pin.McpPackagePin.verify",
                   side_effect=(
                       [True] * (3 if os.name == "nt" else 2) + [False])):
            with LocalMcpHubSession(
                    interpreter_pin=interpreter, package_root=root,
                    package_pin=pin, response_timeout=8) as session:
                process = session._process
                result = session.discover()
                self.assertEqual(result.state, "BINARY_CHANGED")
                self.assertIsNone(result.protocol_version)
                self.assertIsNone(session._process)
                self.assertIsNotNone(process.poll())

    def test_trusted_entrypoint_package_digest_is_checked_before_process_spawn(self):
        from holding.mcp_hub_session import LocalMcpHubSession, McpSessionError
        from holding.mcp_local_inventory import PinnedLocalPlugin
        import hashlib
        import sys
        installed_root = Path(__file__).resolve().parents[1]
        executable = Path(sys.executable).resolve()
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        interpreter = PinnedLocalPlugin("hub-python", "3.13", executable, digest)
        real = build_installation_module_pins(installed_root)
        forged = McpPackagePin.from_manifest(dict(
            real.entries, **{"mcp_entry.py": "0" * 64}))
        with patch("holding.mcp_hub_session._spawn_hub") as spawn:
            with self.assertRaisesRegex(ValueError, "code digest changed"):
                LocalMcpHubSession(
                    interpreter_pin=interpreter,
                    package_root=installed_root,
                    package_pin=forged)
            spawn.assert_not_called()

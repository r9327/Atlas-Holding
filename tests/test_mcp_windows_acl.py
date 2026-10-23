"""Native Windows ACL protection of manifest and cache content."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from holding.mcp_bootstrap import McpBootstrapError, load_bootstrap_manifest
from holding.mcp_windows_acl import (
    WindowsAclError, current_windows_user_sid, require_private_windows_path,
)


def apply_private_acl(path: Path, *, directory: bool = False) -> None:
    """Test-only icacls provisioning; production only reads security descriptors."""
    sid = current_windows_user_sid()
    if directory:
        permissions = "(OI)(CI)F"
    else:
        permissions = "F"
    commands = [
        ["icacls", str(path), "/inheritance:r"],
        ["icacls", str(path), "/grant:r",
         f"*{sid}:{permissions}", f"*S-1-5-18:{permissions}",
         f"*S-1-5-32-544:{permissions}"],
    ]
    for args in commands:
        subprocess.run(args, check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=10)
    subprocess.run(
        ["icacls", str(path), "/remove:g",
         "*S-1-1-0", "*S-1-5-11", "*S-1-5-32-545", "*S-1-3-0"],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10)


@unittest.skipUnless(os.name == "nt", "Native Windows DACL inspection")
class WindowsPrivateAclTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / "private-manifest.json"
        self.file.write_text(json.dumps({
            "schema_version": 1, "cache_database": None, "pins": [],
        }), encoding="utf-8")
        apply_private_acl(self.root, directory=True)
        apply_private_acl(self.file)

    def test_private_file_and_private_parent_admitted(self):
        require_private_windows_path(self.file)
        config = load_bootstrap_manifest(self.file)
        self.assertEqual(config.pins, ())
        self.assertIsNone(config.cache_database)

    def test_world_write_to_manifest_is_rejected_before_parsing(self):
        subprocess.run(
            ["icacls", str(self.file), "/grant", "*S-1-1-0:W"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        with self.assertRaisesRegex(WindowsAclError, "untrusted write"):
            require_private_windows_path(self.file)
        with self.assertRaises(McpBootstrapError):
            load_bootstrap_manifest(self.file)

    def test_world_write_to_parent_allows_replacement_and_is_rejected(self):
        subprocess.run(
            ["icacls", str(self.root), "/grant", "*S-1-1-0:(OI)(CI)M"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        with self.assertRaisesRegex(WindowsAclError, "untrusted write"):
            require_private_windows_path(self.file)
        with self.assertRaises(McpBootstrapError):
            load_bootstrap_manifest(self.file)

    def test_symlink_manifest_never_passes_acl_inspection(self):
        fake = self.root / "shortcut.json"
        try:
            fake.symlink_to(self.file)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted")
        with self.assertRaises(WindowsAclError):
            require_private_windows_path(fake)
        with self.assertRaises(McpBootstrapError):
            load_bootstrap_manifest(fake)


class LinuxWindowsAclBoundaryTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Non-Windows boundary")
    def test_native_acl_check_does_not_return_a_fake_pass_elsewhere(self):
        with self.assertRaises(WindowsAclError):
            require_private_windows_path(Path("/tmp/manifest.json"))


if __name__ == "__main__":
    unittest.main()

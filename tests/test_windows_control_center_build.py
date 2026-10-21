"""Tests for nonrelease Windows Qt packaging contract without a build on Linux."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.windows_control_center_build import (
    build_windows_control_center, build_windows_engine, build_windows_agent,
)


class ViewerBuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.holding = self.root / "holding"
        self.holding.mkdir()
        for name in ("control_center_entry.py", "control_center_live.py"):
            (self.holding / name).write_text("# test source\n", encoding="utf-8")
        self.output = self.root / "artifact"

    def fake_packager(self, command, **options):
        self.assertFalse(options["shell"])
        self.assertTrue(options["check"])
        self.assertEqual(options["cwd"], self.root)
        self.assertIn("--onefile", command)
        self.assertIn("--windowed", command)
        self.assertIn("--noupx", command)
        self.assertEqual(command[command.index("--name") + 1],
                         "AtlasHoldingControlCenter")
        dist = Path(command[command.index("--distpath") + 1])
        dist.mkdir()
        (dist / "AtlasHoldingControlCenter.exe").write_bytes(b"Z" * 1_000_001)

    def test_build_is_native_windows_only(self):
        if __import__("sys").platform == "win32":
            self.skipTest("Native build covered in Windows smoke CI")
        with self.assertRaises(RuntimeError):
            build_windows_control_center(
                output_dir=self.output, project_root=self.root)

    def test_exact_windows_bundle_size_sha_and_no_release_claim(self):
        with patch("holding.windows_control_center_build.sys.platform", "win32"), \
             patch("holding.windows_control_center_build.subprocess.run",
                   side_effect=self.fake_packager) as pack:
            result = build_windows_control_center(
                output_dir=self.output, project_root=self.root)
        artifact = self.output / "AtlasHoldingControlCenter.exe"
        self.assertEqual(result.artifact, str(artifact))
        self.assertEqual(result.bytes, 1_000_001)
        self.assertEqual(result.sha256, hashlib.sha256(
            b"Z" * 1_000_001).hexdigest())
        self.assertFalse(result.release_ready)
        self.assertTrue(artifact.is_file())
        pack.assert_called_once()

    def test_engine_and_viewer_share_one_safe_windows_packager(self):
        for name in ("daemon_entry.py", "service.py", "state_store.py"):
            (self.holding / name).write_text("# test source\n", encoding="utf-8")
        seen = []
        def package(command, **options):
            self.assertFalse(options["shell"])
            self.assertTrue(options["check"])
            self.assertEqual(options["cwd"], self.root)
            self.assertIn("--console", command)
            self.assertNotIn("--windowed", command)
            self.assertIn("--onefile", command)
            self.assertEqual(command[command.index("--name") + 1],
                             "AtlasHoldingEngine")
            stage = Path(command[command.index("--distpath") + 1])
            stage.mkdir()
            (stage / "AtlasHoldingEngine.exe").write_bytes(b"E" * 1_000_101)
            seen.append(command)
        with patch("holding.windows_control_center_build.sys.platform", "win32"), \
             patch("holding.windows_control_center_build.subprocess.run",
                   side_effect=package):
            result = build_windows_engine(
                output_dir=self.output, project_root=self.root)
        self.assertEqual(result.bytes, 1_000_101)
        self.assertEqual(result.sha256, hashlib.sha256(
            b"E" * 1_000_101).hexdigest())
        self.assertFalse(result.release_ready)
        self.assertTrue((self.output / "AtlasHoldingEngine.exe").is_file())
        self.assertEqual(len(seen), 1)

    def test_build_frozen_agent_requires_protocol_source_and_real_cli_argv(self):
        (self.holding / "ollama_protocol.py").write_text(
            "# trusted agent protocol source\n", encoding="utf-8")
        def package(command, **options):
            self.assertIn("--console", command)
            self.assertNotIn("--windowed", command)
            self.assertEqual(command[command.index("--name") + 1],
                             "AtlasHoldingAgent")
            entry = Path(command[-1]).read_text(encoding="utf-8")
            self.assertIn("holding.ollama_protocol import main", entry)
            self.assertIn("main(sys.argv[1:])", entry)
            dest = Path(command[command.index("--distpath") + 1])
            dest.mkdir()
            (dest / "AtlasHoldingAgent.exe").write_bytes(b"A" * 1_000_008)
        with patch("holding.windows_control_center_build.sys.platform", "win32"), \
             patch("holding.windows_control_center_build.subprocess.run",
                   side_effect=package):
            proof = build_windows_agent(output_dir=self.output, project_root=self.root)
        self.assertFalse(proof.release_ready)
        self.assertEqual(proof.bytes, 1_000_008)
        self.assertEqual(proof.sha256, hashlib.sha256(b"A" * 1_000_008).hexdigest())
        self.assertTrue((self.output / "AtlasHoldingAgent.exe").is_file())

    def test_never_overwrite_or_package_missing_or_relative_sources(self):
        self.output.mkdir()
        artifact = self.output / "AtlasHoldingControlCenter.exe"
        artifact.write_bytes(b"important existing viewer")
        with patch("holding.windows_control_center_build.sys.platform", "win32"), \
             patch("holding.windows_control_center_build.subprocess.run") as pack:
            with self.assertRaises(FileExistsError):
                build_windows_control_center(
                    output_dir=self.output, project_root=self.root)
            self.assertEqual(artifact.read_bytes(), b"important existing viewer")
            with self.assertRaises(ValueError):
                build_windows_control_center(
                    output_dir=Path("relative"), project_root=self.root)
            (self.holding / "control_center_live.py").unlink()
            with self.assertRaises(ValueError):
                build_windows_control_center(
                    output_dir=self.root / "other", project_root=self.root)
            pack.assert_not_called()


if __name__ == "__main__":
    unittest.main()

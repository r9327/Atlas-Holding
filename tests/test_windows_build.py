"""Windows setup build contract without uploading or running a release artifact."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.windows_build import build_windows_installer


class WindowsBuildTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.manifest = base / "assets.json"
        self.output = base / "dist"
        self.project = base / "project"
        (self.project / "holding").mkdir(parents=True)
        (self.project / "holding" / "windows_setup.py").write_text("pass")
        self.manifest.write_text(json.dumps({
            "version": 1,
            "assets": [{
                "component": "git", "version": "2.47",
                "url": "https://example.invalid/git.zip",
                "sha256": "a" * 64, "download_bytes": 300,
                "unpacked_bytes": 500,
            }],
        }), encoding="utf-8")
        self.digest = hashlib.sha256(self.manifest.read_bytes()).hexdigest()

    def call_build(self):
        return build_windows_installer(
            manifest=self.manifest, expected_manifest_sha256=self.digest,
            output_dir=self.output, project_root=self.project,
        )

    def test_non_windows_refused_before_outputs_created(self):
        with patch("holding.windows_build.sys.platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "Windows"):
                self.call_build()
        self.assertFalse(self.output.exists())

    def test_manifest_pin_mismatch_fails_before_pyinstaller(self):
        self.digest = "0" * 64
        with patch("holding.windows_build.sys.platform", "win32"):
            with patch("holding.windows_build.subprocess.run") as runner:
                with self.assertRaises(ValueError):
                    self.call_build()
        runner.assert_not_called()

    def test_verified_bundle_report_and_do_not_publish_release(self):
        calls = []
        def build(command, *, cwd, check, timeout, shell):
            self.assertTrue(check)
            self.assertFalse(shell)
            self.assertEqual(timeout, 900)
            self.assertIn("--onefile", command)
            self.assertIn("--windowed", command)
            self.assertIn("--noupx", command)
            self.assertIn("AtlasHoldingSetup", command)
            self.assertEqual(cwd, self.project)
            stage = Path(command[command.index("--distpath") + 1])
            stage.mkdir(parents=True)
            pin_path = Path(command[command.index("--add-data") + 1].rsplit(":", 1)[0])
            self.assertEqual(pin_path.name, "release-assets.json")
            self.assertEqual(hashlib.sha256(pin_path.read_bytes()).hexdigest(), self.digest)
            shim = Path(command[-1]).read_text(encoding="utf-8")
            self.assertIn(self.digest, shim)
            self.assertIn("--self-test", shim)
            self.assertIn("--resume-ollama", shim)
            self.assertIn("resume_ollama_at_logon", shim)
            self.assertIn("holding.windows_setup", shim)
            parsed = ast.parse(shim, filename="atlas_windows_entry.py")
            self.assertTrue(parsed.body)
            self.assertNotIn(r"\\n", shim)
            self.assertIn("read_pinned_manifest(manifest,", shim)
            (stage / "AtlasHoldingSetup.exe").write_bytes(b"MZ" + b"0" * 1500)
            calls.append(True)

        with patch("holding.windows_build.sys.platform", "win32"):
            with patch("holding.windows_build.subprocess.run", side_effect=build):
                result = self.call_build()
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.manifest_sha256, self.digest)
        self.assertGreater(result.bytes, 1024)
        self.assertEqual(result.sha256, hashlib.sha256(
            Path(result.artifact).read_bytes()).hexdigest())
        self.assertTrue(Path(result.artifact).is_file())

    def test_embeds_two_pinned_runtime_binaries_inside_single_setup(self):
        runtime = (self.project.parent / "approved-runtime").resolve()
        runtime.mkdir()
        names = ("AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe")
        for idx, name in enumerate(names):
            (runtime / name).write_bytes(bytes([65 + idx]) * 1_000_015)
        pins = {
            name: hashlib.sha256((runtime / name).read_bytes()).hexdigest()
            for name in names
        }
        calls = []
        def build(command, *, cwd, check, timeout, shell):
            self.assertTrue(check)
            self.assertFalse(shell)
            self.assertEqual(cwd, self.project)
            additions = [
                command[i + 1] for i, part in enumerate(command[:-1])
                if part == "--add-data"
            ]
            self.assertEqual(len(additions), 3)
            for name in names:
                observed = [v for v in additions if name in v]
                self.assertEqual(len(observed), 1)
                asset = Path(observed[0].rsplit(os.pathsep, 1)[0])
                self.assertEqual(
                    hashlib.sha256(asset.read_bytes()).hexdigest(), pins[name])
            shim = Path(command[-1]).read_text(encoding="utf-8")
            self.assertIn("verify_runtime_payload(base / 'runtime'", shim)
            for digest in pins.values():
                self.assertIn(digest, shim)
            stage = Path(command[command.index("--distpath") + 1])
            stage.mkdir()
            (stage / "AtlasHoldingSetup.exe").write_bytes(b"MZ" + b"0" * 1600)
            calls.append(True)
        with patch("holding.windows_build.sys.platform", "win32"), \
             patch("holding.windows_build.subprocess.run", side_effect=build):
            result = build_windows_installer(
                manifest=self.manifest, expected_manifest_sha256=self.digest,
                output_dir=self.output, project_root=self.project,
                runtime_directory=runtime, runtime_pins=pins,
            )
        self.assertEqual(len(calls), 1)
        self.assertGreater(result.bytes, 1024)
        self.assertEqual(result.sha256, hashlib.sha256(
            Path(result.artifact).read_bytes()).hexdigest())

    def test_builder_embeds_pinned_adapter_only_with_complete_native_pair(self):
        runtime = (self.project.parent / "approved-runtime").resolve()
        runtime.mkdir()
        names = ("AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe")
        for i, name in enumerate(names):
            (runtime / name).write_bytes(bytes([73 + i]) * 1_000_101)
        pins = {name: hashlib.sha256((runtime / name).read_bytes()).hexdigest()
                for name in names}
        adapter_dir = self.project.parent / "approved-agent"
        adapter_dir.mkdir()
        agent = adapter_dir / "AtlasHoldingAgent.exe"
        agent.write_bytes(b"known verified adapter candidate")
        pin = hashlib.sha256(agent.read_bytes()).hexdigest()
        inspected = []
        def package(command, *, cwd, check, timeout, shell):
            resources = [
                command[i + 1] for i, part in enumerate(command[:-1])
                if part == "--add-data"
            ]
            self.assertEqual(len(resources), 4)
            staged = [v for v in resources if "AtlasHoldingAgent.exe" in v]
            self.assertEqual(len(staged), 1)
            candidate = Path(staged[0].rsplit(os.pathsep, 1)[0])
            self.assertEqual(hashlib.sha256(candidate.read_bytes()).hexdigest(), pin)
            shim = Path(command[-1]).read_text(encoding="utf-8")
            self.assertIn("--adapter-root", shim)
            self.assertIn("--adapter-sha256", shim)
            self.assertIn(pin, shim)
            stage = Path(command[command.index("--distpath") + 1])
            stage.mkdir()
            (stage / "AtlasHoldingSetup.exe").write_bytes(b"MZ" + b"0" * 1600)
            inspected.append(True)
        with patch("holding.windows_build.sys.platform", "win32"), patch(
                "holding.windows_build.subprocess.run", side_effect=package):
            result = build_windows_installer(
                manifest=self.manifest, expected_manifest_sha256=self.digest,
                output_dir=self.output, project_root=self.project,
                runtime_directory=runtime, runtime_pins=pins,
                adapter_source=agent, adapter_sha256=pin,
            )
        self.assertTrue(inspected)
        self.assertTrue(Path(result.artifact).is_file())

    def test_builder_does_not_bundle_unpinned_adapter(self):
        agent = self.project.parent / "AtlasHoldingAgent.exe"
        agent.write_bytes(b"agent")
        with patch("holding.windows_build.sys.platform", "win32"), patch(
                "holding.windows_build.subprocess.run") as package:
            with self.assertRaises(ValueError):
                build_windows_installer(
                    manifest=self.manifest,
                    expected_manifest_sha256=self.digest,
                    output_dir=self.output, project_root=self.project,
                    adapter_source=agent, adapter_sha256="0" * 64,
                )
            package.assert_not_called()

    def test_runtime_tampering_or_partial_pins_refuse_packaging(self):
        runtime = (self.project.parent / "approved-runtime").resolve()
        runtime.mkdir()
        gui = runtime / "AtlasHoldingControlCenter.exe"
        engine = runtime / "AtlasHoldingEngine.exe"
        gui.write_bytes(b"A" * 1_000_021)
        engine.write_bytes(b"B" * 1_000_021)
        pins = {
            gui.name: hashlib.sha256(gui.read_bytes()).hexdigest(),
            engine.name: hashlib.sha256(engine.read_bytes()).hexdigest(),
        }
        for invalid in (
            None,
            {gui.name: pins[gui.name]},
            {**pins, engine.name: "0" * 64},
        ):
            with self.subTest(invalid=invalid):
                with patch("holding.windows_build.sys.platform", "win32"), \
                     patch("holding.windows_build.subprocess.run") as pack:
                    with self.assertRaises(ValueError):
                        build_windows_installer(
                            manifest=self.manifest,
                            expected_manifest_sha256=self.digest,
                            output_dir=self.output, project_root=self.project,
                            runtime_directory=runtime, runtime_pins=invalid,
                        )
                    pack.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_refuses_to_replace_an_existing_installer(self):
        self.output.mkdir()
        destination = self.output / "AtlasHoldingSetup.exe"
        destination.write_bytes(b"original")
        with patch("holding.windows_build.sys.platform", "win32"):
            with patch("holding.windows_build.subprocess.run") as run:
                with self.assertRaises(FileExistsError):
                    self.call_build()
        self.assertEqual(destination.read_bytes(), b"original")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

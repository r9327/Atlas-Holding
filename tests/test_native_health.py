"""Native executable version checks are bounded, pinned and not full readiness."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from holding.native_health import NativeHealth, check_staged_binary
from holding.probe_process import ProbeProcessError
from holding.staged_install import (InstallError, InstallInspection, PinnedZip)


class NativeHealthTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.directory = self.root / "components" / "git" / "2.47"
        (self.directory / "bin").mkdir(parents=True)
        (self.directory / "bin/git.exe").write_bytes(b"trusted fixture bytes")
        self.pin = PinnedZip(
            "git", "2.47", "https://example.invalid/git.zip",
            "a" * 64, 1200, 2000, "bin/git.exe", "2.47",
        )
        self.verified = InstallInspection("VERIFIED_PAYLOAD", self.directory, 1)

    def test_verified_native_exe_executes_version_with_bounded_runner(self):
        with patch("holding.native_health.inspect_versioned_zip",
                   return_value=self.verified) as inspect:
            with patch("holding.native_health.run_probe",
                       return_value=subprocess.CompletedProcess(
                           ["git.exe", "--version"], 0, "git version 2.47", "",
                       )) as runner:
                result = check_staged_binary(self.pin, install_root=self.root)
        self.assertEqual(result, NativeHealth("git", "BINARY_VERSION_PROBED"))
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(runner.call_args.args[0][-1], "--version")
        self.assertEqual(runner.call_args.kwargs["max_output_bytes"], 8192)
        self.assertFalse(runner.call_args.kwargs["environment"].get("GITHUB_TOKEN"))

    def test_unverified_payload_cannot_run_executable(self):
        with patch("holding.native_health.inspect_versioned_zip",
                   return_value=InstallInspection("BLOCKED_INVALID", self.directory)):
            with patch("holding.native_health.run_probe") as runner:
                result = check_staged_binary(self.pin, install_root=self.root)
        self.assertEqual(result.state, "BLOCKED_INVALID")
        runner.assert_not_called()

    def test_binary_version_mismatch_is_not_success(self):
        with patch("holding.native_health.inspect_versioned_zip",
                   return_value=self.verified):
            with patch("holding.native_health.run_probe",
                       return_value=subprocess.CompletedProcess(
                           [], 0, "git version 1.0", "",
                       )):
                result = check_staged_binary(self.pin, install_root=self.root)
        self.assertEqual(result.state, "BINARY_VERSION_MISMATCH")

    def test_timeout_and_runner_error_fail_closed(self):
        for failure in (
            subprocess.TimeoutExpired(["git.exe"], 2),
            ProbeProcessError("output exceeded"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with patch("holding.native_health.inspect_versioned_zip",
                           return_value=self.verified):
                    with patch("holding.native_health.run_probe",
                               side_effect=failure):
                        result = check_staged_binary(self.pin,
                                                     install_root=self.root)
                self.assertEqual(result.state, "BINARY_PROBE_FAILED")

    def test_change_detected_after_probe(self):
        states = [self.verified, InstallInspection("BLOCKED_INVALID", self.directory)]
        with patch("holding.native_health.inspect_versioned_zip",
                   side_effect=states):
            with patch("holding.native_health.run_probe",
                       return_value=subprocess.CompletedProcess(
                           [], 0, "git version 2.47", "",
                       )):
                result = check_staged_binary(self.pin, install_root=self.root)
        self.assertEqual(result.state, "BINARY_MODIFIED_DURING_PROBE")

    def test_unsafe_link_or_missing_executable_is_rejected(self):
        (self.directory / "bin/git.exe").unlink()
        with patch("holding.native_health.inspect_versioned_zip",
                   return_value=self.verified):
            with patch("holding.native_health.run_probe") as runner:
                result = check_staged_binary(self.pin, install_root=self.root)
        self.assertEqual(result.state, "BINARY_UNSAFE")
        runner.assert_not_called()

    def test_invalid_runtime_metadata_refused(self):
        with self.assertRaises(InstallError):
            PinnedZip("git", "2.47", "https://example.invalid/f",
                      "a" * 64, 1, 1, "../outside.exe", "2.47").validate()
        with self.assertRaises(InstallError):
            PinnedZip("git", "2.47", "https://example.invalid/f",
                      "a" * 64, 1, 1, "bin/runner.cmd", "2.47").validate()
        with self.assertRaises(InstallError):
            PinnedZip("git", "2.47", "https://example.invalid/f",
                      "a" * 64, 1, 1, "bin/git.exe").validate()

    def test_plugin_binary_probe_not_permitted(self):
        custom = PinnedZip(
            "another-plugin", "1.0", "https://example.invalid/a.zip",
            "a" * 64, 1, 1, "bin/plugin.exe", "1.0",
        )
        with patch("holding.native_health.run_probe") as runner:
            result = check_staged_binary(custom, install_root=self.root)
        self.assertEqual(result.state, "NATIVE_PROBE_NOT_ALLOWED")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()

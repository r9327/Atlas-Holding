"""Winget adapter permits only exact official pinned packages."""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from holding.windows_package_manager import (
    WingetPin, WindowsPackageError, install_pinned_winget,
)


class WindowsPackageManagerTests(unittest.TestCase):
    def setUp(self):
        self.pin = WingetPin("git", "Git.Git", "2.55.0")

    def test_no_consent_or_network_cannot_start_winget(self):
        for authorized, online, expected in (
            (False, True, "AUTH_REQUIRED"),
            (True, False, "NETWORK_REQUIRED"),
        ):
            with self.subTest(auth=authorized, online=online):
                with patch("holding.windows_package_manager.run_probe") as runner:
                    result = install_pinned_winget(
                        self.pin, authorized=authorized, online=online,
                    )
                self.assertEqual(result.state, expected)
                runner.assert_not_called()

    def test_untrusted_package_ids_and_versions_are_rejected(self):
        for bad in (
            WingetPin("git", "Microsoft.Git", "2.55.0"),
            WingetPin("opencode", "SST.OpenCodeDesktop", "1.1.0"),
            WingetPin("ollama", "Ollama.Ollama", "latest"),
            WingetPin("git", "Git.Git;rm -rf", "2.55.0"),
            WingetPin("python", "Python.Python.3.13", "3.13.1"),
        ):
            with self.subTest(pin=bad):
                with self.assertRaises(WindowsPackageError):
                    bad.validate()

    def test_exact_user_scoped_package_command_and_no_secret_environment(self):
        result = subprocess.CompletedProcess(["winget"], 0, "winget 1.2", "")
        with patch("holding.windows_package_manager.platform.system",
                   return_value="Windows"):
            with patch("holding.windows_package_manager.shutil.which",
                       return_value="C:\\Windows\\System32\\winget.exe"):
                with patch("holding.windows_package_manager.os.path.isabs",
                           return_value=True):
                    with patch("holding.windows_package_manager.run_probe",
                               side_effect=[result, result, result]) as runner:
                        installed = install_pinned_winget(
                            self.pin, authorized=True, online=True,
                        )
        self.assertEqual(installed.state, "INSTALLED_NOT_HEALTHCHECKED")
        self.assertEqual(runner.call_count, 3)
        args = runner.call_args.args[0]
        self.assertEqual(args[1:5], ["install", "--exact", "--id", "Git.Git"])
        self.assertIn("2.55.0", args)
        self.assertIn("--scope", args)
        self.assertEqual(args[args.index("--scope") + 1], "user")
        self.assertIn("--source", args)
        self.assertEqual(args[args.index("--source") + 1], "winget")
        self.assertNotIn("--force", args)
        self.assertFalse(runner.call_args.kwargs.get("shell", False))
        self.assertTrue(runner.call_args.kwargs["timeout"] <= 900)
        self.assertNotIn("GITHUB_TOKEN",
                         runner.call_args.kwargs["environment"])

    def test_unavailable_exact_user_scope_package_refuses_install(self):
        healthy = subprocess.CompletedProcess([], 0, "v1.0", "")
        absent = subprocess.CompletedProcess([], 1, "", "version missing")
        with patch("holding.windows_package_manager.platform.system",
                   return_value="Windows"):
            with patch("holding.windows_package_manager.shutil.which",
                       return_value="C:\\Windows\\System32\\winget.exe"):
                with patch("holding.windows_package_manager.os.path.isabs",
                           return_value=True):
                    with patch("holding.windows_package_manager.run_probe",
                               side_effect=[healthy, absent]) as runner:
                        state = install_pinned_winget(
                            self.pin, authorized=True, online=True,
                        )
        self.assertEqual(state.state, "PINNED_USER_PACKAGE_UNAVAILABLE")
        self.assertEqual(runner.call_count, 2)
        show = runner.call_args.args[0]
        self.assertIn("show", show)
        self.assertIn("2.55.0", show)
        self.assertIn("--architecture", show)
        self.assertIn("x64", show)
        self.assertIn("--scope", show)
        self.assertEqual(show[show.index("--scope") + 1], "user")

    def test_ollama_installer_inherits_only_approved_model_disk(self):
        from pathlib import Path
        from holding.model_volume import ModelVolumeInspection
        models = Path("C:/Atlas Models")
        pin = WingetPin("ollama", "Ollama.Ollama", "0.32.8")
        good = subprocess.CompletedProcess([], 0, "v1", "")
        with patch("holding.windows_package_manager.platform.system",
                   return_value="Windows"):
            with patch("holding.windows_package_manager.shutil.which",
                       return_value="C:\\winget.exe"):
                with patch("holding.windows_package_manager.os.path.isabs",
                           return_value=True):
                    with patch("holding.model_volume.inspect_model_volume",
                               return_value=ModelVolumeInspection(
                                   "VOLUME_AVAILABLE", 20 * 1024**3,
                               )):
                        with patch("holding.windows_package_manager.run_probe",
                                   side_effect=[good, good, good]) as runner:
                            installed = install_pinned_winget(
                                pin, authorized=True, online=True,
                                model_root=models,
                            )
        self.assertEqual(installed.state, "INSTALLED_NOT_HEALTHCHECKED")
        self.assertEqual(
            runner.call_args.kwargs["environment"]["OLLAMA_MODELS"],
            str(models),
        )

    def test_other_packages_cannot_override_models_directory(self):
        from pathlib import Path
        with self.assertRaises(WindowsPackageError):
            install_pinned_winget(
                self.pin, authorized=True, online=True,
                model_root=Path("C:/models"),
            )

    def test_manager_missing_or_unsupported_os_does_not_install(self):
        with patch("holding.windows_package_manager.platform.system",
                   return_value="Linux"):
            with patch("holding.windows_package_manager.run_probe") as runner:
                result = install_pinned_winget(
                    self.pin, authorized=True, online=True,
                )
        self.assertEqual(result.state, "WINDOWS_REQUIRED")
        runner.assert_not_called()
        with patch("holding.windows_package_manager.platform.system",
                   return_value="Windows"):
            with patch("holding.windows_package_manager.shutil.which",
                       return_value=None):
                result = install_pinned_winget(
                    self.pin, authorized=True, online=True,
                )
        self.assertEqual(result.state, "WINGET_UNAVAILABLE")

    def test_bad_exit_and_timeout_do_not_claim_installation(self):
        for failure, expected in (
            (subprocess.CompletedProcess([], 4, "bad", ""), "INSTALL_FAILED"),
            (subprocess.TimeoutExpired(["winget"], 30), "INSTALL_INTERRUPTED"),
        ):
            with self.subTest(result=expected):
                with patch("holding.windows_package_manager.platform.system",
                           return_value="Windows"):
                    with patch("holding.windows_package_manager.shutil.which",
                               return_value="C:\\winget.exe"):
                        with patch("holding.windows_package_manager.os.path.isabs",
                                   return_value=True):
                            preflight = subprocess.CompletedProcess(
                                [], 0, "v1.0", "",
                            )
                            with patch("holding.windows_package_manager.run_probe",
                                       side_effect=[preflight, preflight, failure]):
                                got = install_pinned_winget(
                                    self.pin, authorized=True, online=True,
                                )
                self.assertEqual(got.state, expected)


if __name__ == "__main__":
    unittest.main()

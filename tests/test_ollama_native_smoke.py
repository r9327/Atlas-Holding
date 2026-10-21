"""Native Ollama installation smoke can only run on a disposable runner."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component
from holding.ollama_native_smoke import _runner_gate, verify_real_ollama
from holding.ollama_service import OllamaLaunch
from holding.windows_package_manager import WingetResult
from holding.winget_binary import WingetBinary
from test_provision_plan import sample


class Child:
    def __init__(self):
        self.stopped = False

    def poll(self):
        return 1 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def kill(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 1


class NativeOllamaSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = {
            "GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(self.root),
        }

    def test_disposable_runner_gate_blocks_regular_computers(self):
        with patch("holding.ollama_native_smoke.platform.system",
                   return_value="Windows"):
            with patch.dict("holding.ollama_native_smoke.os.environ",
                            {"GITHUB_ACTIONS": "false",
                             "RUNNER_TEMP": str(self.root)}):
                with self.assertRaisesRegex(RuntimeError, "runner"):
                    _runner_gate()
        with patch("holding.ollama_native_smoke.platform.system",
                   return_value="Linux"):
            with patch.dict("holding.ollama_native_smoke.os.environ",
                            self.environment):
                with self.assertRaisesRegex(RuntimeError, "runner"):
                    _runner_gate()

    def test_existing_daemon_never_installed_over_or_stopped(self):
        with patch("holding.ollama_native_smoke._runner_gate"):
            with patch("holding.ollama_native_smoke.scan",
                       return_value=sample(ollama=Component(
                           "FOUND_VALID", path="C:/existing/ollama.exe",
                       ))):
                with patch("holding.ollama_native_smoke._local_api_alive",
                           return_value=True):
                    with patch("holding.ollama_native_smoke.install_pinned_winget") as install:
                        with self.assertRaisesRegex(RuntimeError, "Pre-existing"):
                            verify_real_ollama()
        install.assert_not_called()

    def test_installation_failure_never_claims_api(self):
        no_server = sample(ollama=Component("NOT_FOUND"), models=())
        with patch("holding.ollama_native_smoke._runner_gate"):
            with patch("holding.ollama_native_smoke.scan",
                       return_value=no_server):
                with patch("holding.ollama_native_smoke._local_api_alive",
                           return_value=False):
                    with patch("holding.ollama_native_smoke.discover_winget_binary",
                               return_value=WingetBinary("NO_PINNED_BINARY_FOUND")):
                        with patch("holding.ollama_native_smoke.install_pinned_winget",
                                   return_value=WingetResult(
                                       "ollama", "INSTALL_FAILED",
                                   )):
                            with patch("holding.ollama_native_smoke.start_local_ollama") as start:
                                with self.assertRaisesRegex(RuntimeError, "INSTALL_FAILED"):
                                    verify_real_ollama()
        start.assert_not_called()

    def test_real_daemon_receipt_requires_healthy_version_and_empty_tags(self):
        no_server = sample(ollama=Component("NOT_FOUND"), models=())
        child = Child()
        binary = WingetBinary(
            "EXACT_BINARY_VERIFIED",
            str(self.root / "ollama.exe"), "ollama 0.32.8",
        )
        values = [
            WingetBinary("NO_PINNED_BINARY_FOUND"),
            binary,
        ]
        with patch("holding.ollama_native_smoke._runner_gate"):
            with patch("holding.ollama_native_smoke.scan",
                       return_value=no_server):
                with patch("holding.ollama_native_smoke._local_api_alive",
                           return_value=False):
                    with patch("holding.ollama_native_smoke.discover_winget_binary",
                               side_effect=values):
                        with patch("holding.ollama_native_smoke.install_pinned_winget",
                                   return_value=WingetResult(
                                       "ollama", "INSTALLED_NOT_HEALTHCHECKED",
                                   )) as install:
                            with patch("holding.ollama_native_smoke.start_local_ollama",
                                       return_value=OllamaLaunch(
                                           "OWNED_API_HEALTHY",
                                           str(self.root / "models"), child,
                                       )) as start:
                                with patch("holding.ollama_native_smoke._api_json",
                                           side_effect=[
                                               {"version": "0.32.8"},
                                               {"models": []},
                                           ]):
                                    result = verify_real_ollama()
        self.assertEqual(result["state"], "OLLAMA_WINDOWS_NATIVE_API_PASS")
        self.assertTrue(child.stopped)
        self.assertTrue(install.call_args.kwargs["authorized"])
        self.assertTrue(start.call_args.kwargs["authorized"])
        self.assertEqual(start.call_args.kwargs["pins"][0].version, "0.32.8")

    def test_winget_installer_auto_starts_server_without_storage_claim(self):
        no_server = sample(ollama=Component("NOT_FOUND"), models=())
        binary = WingetBinary(
            "EXACT_BINARY_VERIFIED",
            str(self.root / "ollama.exe"), "ollama 0.32.8",
        )
        with patch("holding.ollama_native_smoke._runner_gate"):
            with patch("holding.ollama_native_smoke.scan",
                       return_value=no_server):
                with patch("holding.ollama_native_smoke._local_api_alive",
                           side_effect=[False, True]):
                    with patch("holding.ollama_native_smoke.discover_winget_binary",
                               side_effect=[
                                   WingetBinary("NO_PINNED_BINARY_FOUND"),
                                   binary,
                               ]):
                        with patch("holding.ollama_native_smoke.install_pinned_winget",
                                   return_value=WingetResult(
                                       "ollama", "INSTALLED_NOT_HEALTHCHECKED",
                                   )):
                            with patch("holding.ollama_native_smoke.start_local_ollama") as launch:
                                with patch("holding.ollama_native_smoke._api_json",
                                           side_effect=[
                                               {"version": "0.32.8"},
                                               {"models": []},
                                           ]):
                                    result = verify_real_ollama()
        self.assertEqual(
            result["state"],
            "OLLAMA_WINDOWS_AUTOSTART_API_PASS_STORAGE_UNVERIFIED",
        )
        self.assertEqual(result["model_volume"], "NOT_ATTESTED")
        launch.assert_not_called()

    def test_port_race_with_new_external_daemon_never_claims_owned(self):
        no_server = sample(ollama=Component("NOT_FOUND"), models=())
        with patch("holding.ollama_native_smoke._runner_gate"):
            with patch("holding.ollama_native_smoke.scan",
                       return_value=no_server):
                with patch("holding.ollama_native_smoke._local_api_alive",
                           return_value=False):
                    with patch("holding.ollama_native_smoke.discover_winget_binary",
                               side_effect=[
                                   WingetBinary("NO_PINNED_BINARY_FOUND"),
                                   WingetBinary(
                                       "EXACT_BINARY_VERIFIED",
                                       str(self.root / "ollama.exe"), "ollama 0.32.8",
                                   ),
                               ]):
                        with patch("holding.ollama_native_smoke.install_pinned_winget",
                                   return_value=WingetResult(
                                       "ollama", "INSTALLED_NOT_HEALTHCHECKED",
                                   )):
                            with patch("holding.ollama_native_smoke.start_local_ollama",
                                       return_value=OllamaLaunch(
                                           "EXISTING_API_STORAGE_UNVERIFIED",
                                           str(self.root), None,
                                       )):
                                with patch("holding.ollama_native_smoke._api_json",
                                           side_effect=[
                                               {"version": "0.32.8"},
                                               {"models": []},
                                           ]):
                                    result = verify_real_ollama()
        self.assertEqual(
            result["state"],
            "OLLAMA_WINDOWS_AUTOSTART_API_PASS_STORAGE_UNVERIFIED",
        )

    def test_installer_daemon_delayed_port_bind_after_our_child_exits(self):
        no_server = sample(ollama=Component("NOT_FOUND"), models=())
        with patch("holding.ollama_native_smoke._runner_gate"):
            with patch("holding.ollama_native_smoke.scan",
                       return_value=no_server):
                with patch("holding.ollama_native_smoke._local_api_alive",
                           side_effect=[False, False, False, True]):
                    with patch("holding.ollama_native_smoke.time.sleep"):
                        with patch("holding.ollama_native_smoke.discover_winget_binary",
                                   side_effect=[
                                       WingetBinary("NO_PINNED_BINARY_FOUND"),
                                       WingetBinary(
                                           "EXACT_BINARY_VERIFIED",
                                           str(self.root / "ollama.exe"),
                                           "ollama 0.32.8",
                                       ),
                                   ]):
                            with patch("holding.ollama_native_smoke.install_pinned_winget",
                                       return_value=WingetResult(
                                           "ollama", "INSTALLED_NOT_HEALTHCHECKED",
                                       )):
                                with patch("holding.ollama_native_smoke.start_local_ollama",
                                           return_value=OllamaLaunch(
                                               "PROCESS_EXITED", str(self.root),
                                           )):
                                    with patch("holding.ollama_native_smoke._api_json",
                                               side_effect=[
                                                   {"version": "0.32.8"},
                                                   {"models": []},
                                               ]):
                                        result = verify_real_ollama()
        self.assertEqual(
            result["state"],
            "OLLAMA_WINDOWS_AUTOSTART_API_PASS_STORAGE_UNVERIFIED",
        )
        self.assertEqual(result["model_volume"], "NOT_ATTESTED")

    def test_failed_child_and_absent_api_remain_hard_failure(self):
        no_server = sample(ollama=Component("NOT_FOUND"), models=())
        with patch("holding.ollama_native_smoke._runner_gate"):
            with patch("holding.ollama_native_smoke.scan",
                       return_value=no_server):
                with patch("holding.ollama_native_smoke._local_api_alive",
                           return_value=False):
                    with patch("holding.ollama_native_smoke.time.sleep"):
                        with patch("holding.ollama_native_smoke.discover_winget_binary",
                                   side_effect=[
                                       WingetBinary("NO_PINNED_BINARY_FOUND"),
                                       WingetBinary(
                                           "EXACT_BINARY_VERIFIED",
                                           str(self.root / "ollama.exe"),
                                           "ollama 0.32.8",
                                       ),
                                   ]):
                            with patch("holding.ollama_native_smoke.install_pinned_winget",
                                       return_value=WingetResult(
                                           "ollama", "INSTALLED_NOT_HEALTHCHECKED",
                                       )):
                                with patch("holding.ollama_native_smoke.start_local_ollama",
                                           return_value=OllamaLaunch(
                                               "PROCESS_EXITED", str(self.root),
                                           )):
                                    with self.assertRaisesRegex(
                                            RuntimeError, "PROCESS_EXITED"):
                                        verify_real_ollama()

    def test_wrong_api_version_blocks_and_kills_our_child_only(self):
        no_server = sample(ollama=Component("NOT_FOUND"), models=())
        child = Child()
        with patch("holding.ollama_native_smoke._runner_gate"):
            with patch("holding.ollama_native_smoke.scan",
                       return_value=no_server):
                with patch("holding.ollama_native_smoke._local_api_alive",
                           return_value=False):
                    with patch("holding.ollama_native_smoke.discover_winget_binary",
                               side_effect=[
                                   WingetBinary("NO_PINNED_BINARY_FOUND"),
                                   WingetBinary(
                                       "EXACT_BINARY_VERIFIED",
                                       str(self.root / "ollama.exe"), "ollama 0.32.8",
                                   ),
                               ]):
                        with patch("holding.ollama_native_smoke.install_pinned_winget",
                                   return_value=WingetResult(
                                       "ollama", "INSTALLED_NOT_HEALTHCHECKED",
                                   )):
                            with patch("holding.ollama_native_smoke.start_local_ollama",
                                       return_value=OllamaLaunch(
                                           "OWNED_API_HEALTHY", str(self.root), child,
                                       )):
                                with patch("holding.ollama_native_smoke._api_json",
                                           side_effect=[
                                               {"version": "0.32.81"},
                                               {"models": []},
                                           ]):
                                    with self.assertRaisesRegex(RuntimeError, "API"):
                                        verify_real_ollama()
        self.assertTrue(child.stopped)


if __name__ == "__main__":
    unittest.main()

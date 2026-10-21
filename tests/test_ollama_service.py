"""Startup owns only processes created by Atlas and pins the model volume."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component
from holding.ollama_service import (
    _minimal_ollama_environment, start_local_ollama,
)
from test_provision_plan import sample


class DummyProcess:
    def __init__(self, *, code=None):
        self.code = code
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 1

    def kill(self):
        self.killed = True
        self.code = 9

    def wait(self, timeout=None):
        self.waited = True
        return self.code


class OllamaServiceTests(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.install = self.root / "Atlas Holding"
        self.models = self.root / "Other Models"
        self.exe = self.root / "ollama.exe"
        self.exe.write_bytes(b"fixture")
        self.snapshot = sample(ollama=Component(
            "FOUND_BROKEN", path=str(self.exe), version="0.12",
            reason="Ollama installed but local API is not healthy",
        ))

    def run_service(self, *, cancelled=None):
        with patch("holding.ollama_service.platform.system",
                   return_value="Windows"):
            with patch("holding.ollama_service.shutil.disk_usage",
                       return_value=type("Disk", (), {"free": 10 * 1024**3})()):
                return start_local_ollama(
                    self.snapshot, install_root=self.install,
                    model_root=self.models, authorized=True,
                    cancelled=cancelled, startup_seconds=2,
                )

    def test_existing_daemon_cannot_claim_chosen_models_path(self):
        with patch("holding.ollama_service._local_api_alive",
                   return_value=True):
            with patch("holding.ollama_service.subprocess.Popen") as launch:
                result = self.run_service()
        self.assertEqual(result.state, "EXISTING_API_STORAGE_UNVERIFIED")
        self.assertFalse(result.owned)
        self.assertFalse(self.models.exists())
        launch.assert_not_called()

    def test_requires_authorization_before_writing_models_directory(self):
        with patch("holding.ollama_service.platform.system",
                   return_value="Windows"):
            with patch("holding.ollama_service._local_api_alive",
                       return_value=False):
                with patch("holding.ollama_service.subprocess.Popen") as launch:
                    result = start_local_ollama(
                        self.snapshot, install_root=self.install,
                        model_root=self.models, authorized=False,
                    )
        self.assertEqual(result.state, "AUTH_REQUIRED")
        launch.assert_not_called()
        self.assertFalse(self.models.exists())

    def test_new_daemon_is_loopback_only_and_uses_selected_model_disk(self):
        child = DummyProcess()
        with patch("holding.ollama_service._local_api_alive",
                   side_effect=[False, True]):
            with patch("holding.ollama_service.subprocess.Popen",
                       return_value=child) as popen:
                result = self.run_service()
        self.assertEqual(result.state, "OWNED_API_HEALTHY")
        self.assertTrue(result.owned)
        self.assertFalse(child.terminated)
        self.assertTrue(self.models.is_dir())
        argv = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        self.assertEqual(argv, [str(self.exe), "serve"])
        self.assertEqual(kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertEqual(kwargs["env"]["OLLAMA_MODELS"], str(self.models))
        self.assertFalse(kwargs["shell"])

    def test_cancelled_launch_terminates_only_owned_child(self):
        child = DummyProcess()
        with patch("holding.ollama_service._local_api_alive",
                   return_value=False):
            with patch("holding.ollama_service.subprocess.Popen",
                       return_value=child):
                result = self.run_service(cancelled=lambda: True)
        self.assertEqual(result.state, "CANCELLED")
        self.assertFalse(child.terminated)

    def test_early_child_failure_is_reported_and_reaped(self):
        child = DummyProcess(code=2)
        with patch("holding.ollama_service._local_api_alive",
                   return_value=False):
            with patch("holding.ollama_service.subprocess.Popen",
                       return_value=child):
                result = self.run_service()
        self.assertEqual(result.state, "PROCESS_EXITED")
        self.assertTrue(child.waited)

    def test_low_disk_blocks_before_model_folder_creation(self):
        with patch("holding.ollama_service.platform.system",
                   return_value="Windows"):
            with patch("holding.ollama_service._local_api_alive",
                       return_value=False):
                with patch("holding.ollama_service.shutil.disk_usage",
                           return_value=type("Disk", (), {"free": 1})()):
                    with patch("holding.ollama_service.subprocess.Popen") as launch:
                        result = start_local_ollama(
                            self.snapshot, install_root=self.install,
                            model_root=self.models, authorized=True,
                        )
        self.assertEqual(result.state, "MODEL_DISK_LOW")
        launch.assert_not_called()
        self.assertFalse(self.models.exists())

    def test_environment_does_not_inherit_hostile_network_variables(self):
        with patch.dict("holding.ollama_service.os.environ",
                        {"OLLAMA_HOST": "0.0.0.0:11434",
                         "OLLAMA_MODELS": "C:/unexpected",
                         "OLLAMA_ORIGINS": "*", "GITHUB_TOKEN": "secret"}):
            env = _minimal_ollama_environment(self.models)
        self.assertEqual(env["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertEqual(env["OLLAMA_MODELS"], str(self.models))
        self.assertNotIn("OLLAMA_ORIGINS", env)
        self.assertNotIn("GITHUB_TOKEN", env)

    def test_pinned_winget_ollama_starts_without_existing_path(self):
        from holding.windows_package_manager import WingetPin
        from holding.winget_binary import WingetBinary
        from dataclasses import replace
        missing = replace(self.snapshot, ollama=Component("NOT_FOUND"))
        pin = WingetPin("ollama", "Ollama.Ollama", "0.32.8")
        child = DummyProcess()
        with patch("holding.ollama_service.platform.system",
                   return_value="Windows"):
            with patch("holding.ollama_service._local_api_alive",
                       side_effect=[False, True]):
                with patch("holding.ollama_service.discover_winget_binary",
                           return_value=WingetBinary(
                               "EXACT_BINARY_VERIFIED",
                               str(self.exe), "ollama 0.32.8",
                           )) as locate:
                    with patch("holding.ollama_service.shutil.disk_usage",
                               return_value=type(
                                   "Disk", (), {"free": 10 * 1024**3})()):
                        with patch("holding.ollama_service.subprocess.Popen",
                                   return_value=child) as popen:
                            state = start_local_ollama(
                                missing, install_root=self.install,
                                model_root=self.models,
                                pins=(pin,), authorized=True,
                            )
        self.assertEqual(state.state, "OWNED_API_HEALTHY")
        locate.assert_called_once_with(pin)
        self.assertEqual(popen.call_args.args[0],
                         [str(self.exe), "serve"])

    def test_invalid_relative_model_path_rejected(self):
        with self.assertRaises(ValueError):
            start_local_ollama(
                self.snapshot, install_root=self.install,
                model_root=Path("relative"), authorized=False,
            )


if __name__ == "__main__":
    unittest.main()

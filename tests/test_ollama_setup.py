"""Model-volume integration: no takeover of existing Ollama daemons."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component
from holding.ollama_service import OllamaLaunch
from holding.ollama_storage import StoredModelDirectory
from holding.ollama_setup import ensure_selected_model_volume
from holding.provision_runtime import ProvisionExecution, ProvisionStep
from test_provision_plan import sample


class OllamaSetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.models = self.root / "models"
        self.result = ProvisionExecution(
            "PAYLOADS_STAGED_NOT_VALIDATED",
            (ProvisionStep("ollama", "START_SERVICE", "SERVICE_START_PENDING"),),
            (),
        )

    def test_existing_daemon_not_restarted_or_moved(self):
        with patch("holding.ollama_setup.start_local_ollama") as launch:
            result, inventory = ensure_selected_model_volume(
                self.result, sample(), install_root=self.root,
                model_root=self.models, pins=(),
            )
        launch.assert_not_called()
        self.assertEqual(result.state, "PAYLOADS_STAGED_NOT_VALIDATED")
        self.assertEqual(result.steps[-1].result,
                         "EXISTING_SERVER_STORAGE_NOT_ATTESTED")
        self.assertFalse(self.models.exists())
        self.assertEqual(inventory.ollama.status, "FOUND_VALID")

    def test_stopped_service_launch_rechecks_the_actual_daemon(self):
        before = sample(ollama=Component(
            "FOUND_BROKEN", path=str(self.root / "ollama.exe"),
            version="0.12",
            reason="Ollama installed but local API is not healthy",
        ))
        after = sample(ollama=Component(
            "FOUND_VALID", version="0.12", reason="Loopback API healthy",
        ))
        fake_launch = OllamaLaunch("OWNED_API_HEALTHY", str(self.models),
                                   process=object())
        with patch("holding.ollama_setup.start_local_ollama",
                   return_value=fake_launch) as launch:
            with patch("holding.ollama_setup.store_model_directory",
                       return_value=StoredModelDirectory(
                           "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED",
                           self.models,
                       )) as store:
                with patch("holding.ollama_setup.scan", return_value=after) as scan:
                    result, inventory = ensure_selected_model_volume(
                        self.result, before, install_root=self.root,
                        model_root=self.models, pins=(),
                    )
        self.assertTrue(store.call_args.kwargs["authorized"])
        self.assertEqual(result.steps[-2].result, "OWNED_API_HEALTHY")
        self.assertEqual(result.steps[-1].result,
                         "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED")
        self.assertEqual(inventory.ollama.status, "FOUND_VALID")
        scan.assert_called_once_with(self.root)
        self.assertTrue(launch.call_args.kwargs["authorized"])
        self.assertEqual(launch.call_args.kwargs["model_root"], self.models)

    def test_start_failure_blocks_every_remaining_health_claim(self):
        before = sample(ollama=Component(
            "FOUND_BROKEN", path=str(self.root / "ollama.exe"),
        ))
        with patch("holding.ollama_setup.start_local_ollama",
                   return_value=OllamaLaunch("START_TIMEOUT", str(self.models))):
            with patch("holding.ollama_setup.scan") as scan:
                result, inventory = ensure_selected_model_volume(
                    self.result, before, install_root=self.root,
                    model_root=self.models, pins=(),
                )
        self.assertEqual(result.state, "PARTIAL_OR_BLOCKED")
        self.assertEqual(result.steps[-1].result, "START_TIMEOUT")
        self.assertTrue(result.blockers)
        scan.assert_not_called()

    def test_relative_model_storage_path_is_rejected(self):
        with self.assertRaises(ValueError):
            ensure_selected_model_volume(
                self.result, sample(), install_root=self.root,
                model_root=Path("relative"), pins=(),
            )


if __name__ == "__main__":
    unittest.main()

"""Ollama API and pinned binary can be rechecked after WinGet without PATH."""
from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component, OllamaModel
from holding.post_ollama_inventory import scan_ollama_with_pins
from holding.windows_package_manager import WingetPin
from holding.winget_binary import WingetBinary
from test_provision_plan import sample

class OllamaManagedInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.pin=WingetPin("ollama","Ollama.Ollama","0.32.8")

    def test_existing_healthy_daemon_is_reused(self):
        with patch("holding.post_ollama_inventory.scan",return_value=sample()):
            with patch("holding.post_ollama_inventory.discover_winget_binary") as discover:
                result=scan_ollama_with_pins(self.root,(self.pin,))
        self.assertEqual(result.ollama.status,"FOUND_VALID")
        discover.assert_not_called()

    def test_new_cli_and_api_are_both_required_for_model_discovery(self):
        missing=sample(ollama=Component("NOT_FOUND"),models=())
        models=(OllamaModel("local:Q4","sha256:test",100),)
        with patch("holding.post_ollama_inventory.scan",return_value=missing):
            with patch("holding.post_ollama_inventory.discover_winget_binary",
                       return_value=WingetBinary(
                           "EXACT_BINARY_VERIFIED",str(self.root/"ollama.exe"),
                           "ollama version 0.32.8")):
                with patch("holding.post_ollama_inventory._ollama",
                           return_value=(Component(
                               "FOUND_VALID",version="0.32.8"),models)) as api:
                    result=scan_ollama_with_pins(self.root,(self.pin,))
        self.assertEqual(result.ollama.status,"FOUND_VALID")
        self.assertEqual(result.models,models)
        self.assertEqual(api.call_args.args[0].status,"FOUND_VALID")

    def test_unverified_executable_never_queries_api(self):
        missing=sample(ollama=Component("NOT_FOUND"),models=())
        with patch("holding.post_ollama_inventory.scan",return_value=missing):
            with patch("holding.post_ollama_inventory.discover_winget_binary",
                       return_value=WingetBinary("NO_PINNED_BINARY_FOUND")):
                with patch("holding.post_ollama_inventory._ollama") as api:
                    actual=scan_ollama_with_pins(self.root,(self.pin,))
        self.assertEqual(actual.ollama.status,"NOT_FOUND")
        api.assert_not_called()

    def test_unhealthy_api_does_not_claim_found_valid(self):
        missing=sample(ollama=Component("NOT_FOUND"),models=())
        with patch("holding.post_ollama_inventory.scan",return_value=missing):
            with patch("holding.post_ollama_inventory.discover_winget_binary",
                       return_value=WingetBinary(
                           "EXACT_BINARY_VERIFIED",str(self.root/"ollama.exe"),
                           "ollama 0.32.8")):
                with patch("holding.post_ollama_inventory._ollama",
                           return_value=(Component("FOUND_BROKEN"),())):
                    actual=scan_ollama_with_pins(self.root,(self.pin,))
        self.assertEqual(actual.ollama.status,"NOT_FOUND")

if __name__=="__main__":
    unittest.main()

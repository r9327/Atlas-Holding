"""Pinned components must remain discoverable across independent process runs."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component, OllamaModel
from holding.managed_toolchain import ManagedTool
from holding.pinned_inventory import restore_pinned_inventory
from holding.windows_package_manager import WingetPin
from holding.winget_binary import WingetBinary
from test_provision_plan import sample


class PinnedInventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.git = WingetPin("git", "Git.Git", "2.55.0")
        self.ollama = WingetPin("ollama", "Ollama.Ollama", "0.32.8")
        self.code = WingetPin("opencode", "SST.opencode", "1.18.29")

    def test_missing_git_and_opencode_restored_without_reinstall(self):
        baseline = sample(
            git=Component("NOT_FOUND"),
            opencode=Component("NOT_FOUND"),
        )
        def locate(pin):
            return WingetBinary(
                "EXACT_BINARY_VERIFIED",
                str(self.root / (pin.component + ".exe")),
                pin.component + " " + pin.version,
            )
        with patch("holding.pinned_inventory.discover_winget_binary",
                   side_effect=locate) as discover:
            actual = restore_pinned_inventory(
                baseline, (self.git, self.code), install_root=self.root,
            )
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(actual.git.status, "FOUND_VALID")
        self.assertEqual(actual.opencode.status, "FOUND_VALID")
        self.assertEqual(actual.python, baseline.python)
        self.assertEqual(actual.models, baseline.models)

    def test_healthy_preexisting_program_is_never_overridden(self):
        original = sample(git=Component(
            "FOUND_VALID", "C:/Other/Git/git.exe", "git 2.54",
        ))
        with patch("holding.pinned_inventory.discover_winget_binary") as find:
            actual = restore_pinned_inventory(
                original, (self.git,), install_root=self.root,
            )
        self.assertIs(actual, original)
        find.assert_not_called()

    def test_unverified_winget_folder_never_satisfies_inventory(self):
        original = sample(opencode=Component("NOT_FOUND"))
        with patch("holding.pinned_inventory.discover_winget_binary",
                   return_value=WingetBinary("NO_PINNED_BINARY_FOUND")):
            actual = restore_pinned_inventory(
                original, (self.code,), install_root=self.root,
            )
        self.assertEqual(actual.opencode.status, "NOT_FOUND")

    def test_verified_stopped_ollama_does_not_lose_unknown_models(self):
        original = sample(
            ollama=Component("NOT_FOUND"), models=(),
        )
        with patch("holding.pinned_inventory.discover_winget_binary",
                   return_value=WingetBinary(
                       "EXACT_BINARY_VERIFIED",
                       str(self.root / "ollama.exe"), "ollama version 0.32.8",
                   )):
            with patch("holding.pinned_inventory._ollama",
                       return_value=(Component(
                           "FOUND_BROKEN", reason="Ollama installed but local API is not healthy",
                       ), ())):
                actual = restore_pinned_inventory(
                    original, (self.ollama,), install_root=self.root,
                )
        self.assertEqual(actual.ollama.status, "FOUND_BROKEN")
        self.assertTrue(actual.ollama.path.endswith("ollama.exe"))
        self.assertEqual(actual.models, ())
        self.assertIn("API is not healthy", actual.ollama.reason)

    def test_ollama_models_only_return_when_api_is_healthy(self):
        original = sample(ollama=Component("NOT_FOUND"), models=())
        models = (OllamaModel("code:Q4", "sha256:test", 100),)
        with patch("holding.pinned_inventory.discover_winget_binary",
                   return_value=WingetBinary(
                       "EXACT_BINARY_VERIFIED",
                       str(self.root / "ollama.exe"), "ollama 0.32.8",
                   )):
            with patch("holding.pinned_inventory._ollama",
                       return_value=(Component("FOUND_VALID"), models)):
                actual = restore_pinned_inventory(
                    original, (self.ollama,), install_root=self.root,
                )
        self.assertEqual(actual.ollama.status, "FOUND_VALID")
        self.assertEqual(actual.models, models)

    def test_broken_existing_tool_is_not_silently_declared_healthy(self):
        baseline = sample(git=Component("FOUND_BROKEN"))
        with patch("holding.pinned_inventory.discover_winget_binary",
                   return_value=WingetBinary("NO_PINNED_BINARY_FOUND")):
            actual = restore_pinned_inventory(
                baseline, (self.git,), install_root=self.root,
            )
        self.assertEqual(actual.git.status, "FOUND_BROKEN")

    def test_requires_trusted_complete_inventory_inputs(self):
        with self.assertRaises(ValueError):
            restore_pinned_inventory(sample(), (object(),), install_root=self.root)
        with self.assertRaises(ValueError):
            restore_pinned_inventory(
                sample(), (self.git,), install_root=Path("relative"),
            )


if __name__ == "__main__":
    unittest.main()

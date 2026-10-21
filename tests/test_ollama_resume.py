"""Windows logon resume uses saved consent and rechecks the local API."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.inventory import Component
from holding.ollama_resume import resume_ollama_at_logon
from holding.ollama_storage import StoredModelDirectory
from holding.ollama_service import OllamaLaunch
from test_provision_plan import sample


class ResumeOllamaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.models = self.root / "models"
        self.models.mkdir()
        self.manifest = self.root / "release-assets.json"
        self.offline = sample(ollama=Component(
            "FOUND_BROKEN", path=str(self.root / "ollama.exe"),
            version="0.12", reason="Ollama installed but local API is not healthy",
        ))

    def resume(self, *, storage="STORED_PREFERENCE_NOT_RUNTIME_ATTESTED",
               snapshots=()):
        saved = StoredModelDirectory(
            storage,
            self.models if storage == "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED"
            else None,
        )
        with patch("holding.ollama_resume.read_pinned_manifest",
                   return_value=()) as verify:
            with patch("holding.ollama_resume.read_model_directory",
                       return_value=saved):
                with patch("holding.ollama_resume.scan",
                           side_effect=list(snapshots)) as scan:
                    with patch("holding.ollama_resume.start_local_ollama") as start:
                        result = resume_ollama_at_logon(
                            manifest=self.manifest,
                            manifest_sha256="a" * 64,
                            install_root=self.root,
                        )
        return result, verify, scan, start

    def test_unconfigured_or_tampered_storage_never_launches(self):
        for state in ("NOT_CONFIGURED", "BLOCKED_INVALID",
                      "MISSING_MODEL_VOLUME"):
            with self.subTest(state=state):
                result, manifest, scan, start = self.resume(storage=state)
                self.assertEqual(result.state, state)
                manifest.assert_called_once()
                scan.assert_not_called()
                start.assert_not_called()

    def test_existing_daemon_does_not_claim_our_storage(self):
        result, _, scan, start = self.resume(snapshots=(sample(),))
        self.assertEqual(result.state, "EXISTING_SERVER_STORAGE_NOT_ATTESTED")
        scan.assert_called_once()
        start.assert_not_called()

    def test_linux_never_starts_windows_service(self):
        result, _, _, start = self.resume(
            snapshots=(sample(os_name="Linux"),))
        self.assertEqual(result.state, "WINDOWS_REQUIRED")
        start.assert_not_called()

    def test_failed_launch_never_claims_health(self):
        with patch("holding.ollama_resume.read_pinned_manifest", return_value=()):
            with patch("holding.ollama_resume.read_model_directory",
                       return_value=StoredModelDirectory(
                           "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED", self.models,
                       )):
                with patch("holding.ollama_resume.scan",
                           return_value=self.offline):
                    with patch("holding.ollama_resume.start_local_ollama",
                               return_value=OllamaLaunch(
                                   "START_TIMEOUT", str(self.models),
                               )) as starter:
                        outcome = resume_ollama_at_logon(
                            manifest=self.manifest,
                            manifest_sha256="a" * 64,
                            install_root=self.root,
                        )
        self.assertEqual(outcome.state, "RESUME_START_TIMEOUT")
        self.assertTrue(starter.call_args.kwargs["authorized"])

    def test_success_requires_service_reinspection(self):
        fake = OllamaLaunch(
            "OWNED_API_HEALTHY", str(self.models), process=object(),
        )
        for after, expected in (
            (sample(), "OWNED_SERVICE_RESUMED"),
            (self.offline, "POST_RESUME_API_UNHEALTHY"),
        ):
            with self.subTest(expected=expected):
                with patch("holding.ollama_resume.read_pinned_manifest",
                           return_value=()):
                    with patch("holding.ollama_resume.read_model_directory",
                               return_value=StoredModelDirectory(
                                   "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED",
                                   self.models,
                               )):
                        with patch("holding.ollama_resume.scan",
                                   side_effect=[self.offline, after]) as scan:
                            with patch("holding.ollama_resume.start_local_ollama",
                                       return_value=fake):
                                result = resume_ollama_at_logon(
                                    manifest=self.manifest,
                                    manifest_sha256="a" * 64,
                                    install_root=self.root,
                                )
                self.assertEqual(result.state, expected)
                self.assertEqual(scan.call_count, 2)

    def test_rejects_relative_install_root_without_probing(self):
        with patch("holding.ollama_resume.read_pinned_manifest") as verify:
            with self.assertRaises(ValueError):
                resume_ollama_at_logon(
                    manifest=self.manifest,
                    manifest_sha256="a" * 64,
                    install_root=Path("relative"),
                )
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()

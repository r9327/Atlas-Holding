"""Model volume preference is atomic, consented and not a daemon receipt."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from holding.ollama_storage import read_model_directory, store_model_directory


class OllamaStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.install = self.root / "Installed Atlas"
        self.models = self.root / "Second Volume Models"
        self.models.mkdir()

    def test_saving_models_path_is_consent_gated(self):
        outcome = store_model_directory(
            self.install, self.models, authorized=False,
        )
        self.assertEqual(outcome.status, "AUTH_REQUIRED")
        self.assertFalse(self.install.exists())

    def test_atomic_preference_survives_next_launch(self):
        saved = store_model_directory(
            self.install, self.models, authorized=True,
        )
        self.assertEqual(saved.status,
                         "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED")
        loaded = read_model_directory(self.install)
        self.assertEqual(loaded.status,
                         "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED")
        self.assertEqual(loaded.directory, self.models)
        folder = self.install / "settings"
        self.assertEqual([item.name for item in folder.iterdir()],
                         ["ollama-model-storage.json"])

    def test_desktop_user_environment_preference_is_readable_but_unattested(self):
        saved = store_model_directory(
            self.install, self.models, authorized=True,
            source="consented_user_ollama_environment",
        )
        self.assertEqual(saved.status,
                         "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED")
        persisted = json.loads((
            self.install / "settings" / "ollama-model-storage.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(persisted["source"],
                         "consented_user_ollama_environment")
        self.assertEqual(read_model_directory(self.install).directory, self.models)

    def test_second_setup_cannot_overwrite_different_existing_volume(self):
        first = store_model_directory(
            self.install, self.models, authorized=True,
        )
        different = self.root / "other"
        different.mkdir()
        refused = store_model_directory(
            self.install, different, authorized=True,
            source="consented_user_ollama_environment",
        )
        self.assertEqual(refused.status, "EXISTING_MODEL_VOLUME_CONFLICT")
        self.assertEqual(refused.directory, self.models)
        self.assertEqual(read_model_directory(self.install).directory, self.models)

    def test_second_setup_does_not_override_existing_origin(self):
        store_model_directory(self.install, self.models, authorized=True)
        outcome = store_model_directory(
            self.install, self.models, authorized=True,
            source="consented_user_ollama_environment",
        )
        self.assertEqual(outcome.status,
                         "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED")
        document = json.loads((
            self.install / "settings" / "ollama-model-storage.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(document["source"],
                         "consented_owned_ollama_service")

    def test_unknown_preference_provenance_is_rejected(self):
        with self.assertRaises(ValueError):
            store_model_directory(
                self.install, self.models, authorized=True,
                source="untrusted_import",
            )

    def test_unknown_schema_or_tampering_blocks(self):
        store_model_directory(self.install, self.models, authorized=True)
        path = self.install / "settings" / "ollama-model-storage.json"
        for data in (
            {"schema": 99, "directory": str(self.models),
             "source": "consented_owned_ollama_service"},
            {"schema": 1, "directory": "relative",
             "source": "consented_owned_ollama_service"},
            {"schema": 1, "directory": str(self.models),
             "source": "injected"},
        ):
            with self.subTest(data=data):
                path.write_text(json.dumps(data))
                self.assertEqual(read_model_directory(self.install).status,
                                 "BLOCKED_INVALID")

    def test_offline_removed_volume_is_reported(self):
        store_model_directory(self.install, self.models, authorized=True)
        self.models.rmdir()
        result = read_model_directory(self.install)
        self.assertEqual(result.status, "MISSING_MODEL_VOLUME")
        self.assertEqual(result.directory, self.models)

    def test_nonexistent_model_volume_cannot_be_persisted(self):
        self.models.rmdir()
        result = store_model_directory(
            self.install, self.models, authorized=True,
        )
        self.assertEqual(result.status, "MODEL_VOLUME_NOT_MOUNTED")
        self.assertFalse(self.install.exists())


if __name__ == "__main__":
    unittest.main()

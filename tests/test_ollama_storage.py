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

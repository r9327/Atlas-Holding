"""Selected model disk is measured before any install or download."""
from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from holding.model_volume import ModelVolumeInspection, inspect_model_volume

class ModelVolumeTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.dest=self.root/"models"/"selected"

    def test_uncreated_models_destination_measured_read_only(self):
        outcome=inspect_model_volume(self.dest)
        self.assertEqual(outcome.status,"VOLUME_AVAILABLE")
        self.assertGreater(outcome.free_bytes,2*1024**3)
        self.assertFalse(self.dest.exists())

    def test_missing_drive_or_non_directory_is_blocked(self):
        file=self.root/"file"
        file.write_text("occupied")
        self.assertEqual(inspect_model_volume(file).status,"NOT_A_DIRECTORY")
        with patch("holding.model_volume._existing_disk",side_effect=ValueError):
            self.assertEqual(inspect_model_volume(self.dest).status,"VOLUME_UNAVAILABLE")

    def test_low_storage_is_reported_without_writes(self):
        with patch("holding.model_volume.shutil.disk_usage",
                   return_value=type("Disk",(),{"free":1024})()):
            outcome=inspect_model_volume(self.dest)
        self.assertEqual(outcome.status,"INSUFFICIENT_SPACE")
        self.assertEqual(outcome.free_bytes,1024)
        self.assertFalse(self.dest.exists())

    def test_relative_path_refused(self):
        self.assertEqual(inspect_model_volume(Path("relative")).status,
                         "ABSOLUTE_PATH_REQUIRED")

    def test_linked_target_is_unusable(self):
        target=self.root/"actual"
        target.mkdir()
        link=self.root/"linked"
        try:
            link.symlink_to(target,target_is_directory=True)
        except (NotImplementedError,OSError):
            self.skipTest("Symlink unavailable on this runner")
        self.assertEqual(inspect_model_volume(link).status,"VOLUME_UNAVAILABLE")

if __name__=="__main__":
    unittest.main()

"""Actual local filesystem observations: never mutate updater state."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from holding.update_observation import LocalUpdateObservation, observe_local_update
from holding.update_stage import stage_release_archive
from holding.update_switch import activate_staged_release


class UpdateObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "installed"
        self.archive = Path(self.temp.name) / "release.zip"

    def stage_and_activate(self):
        with zipfile.ZipFile(self.archive, "w") as bundle:
            bundle.writestr("bin/AtlasHoldingEngine.exe", b"fixture")
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        stage_release_archive(
            self.archive, self.root, release="v2",
            expected_sha256=digest,
        )
        activate_staged_release(
            self.root, release="v2", approved_archive_sha256=digest,
            health_probe=lambda _: True,
        )
        return digest

    def test_not_configured_distinct_from_installed(self):
        self.assertEqual(observe_local_update(None).status, "NOT_CONFIGURED")
        self.assertEqual(observe_local_update(self.root).status, "UNVERIFIED_LOCAL_STATE")
        with self.assertRaises(ValueError):
            observe_local_update(str(self.root))

    def test_local_staged_receipt_has_no_false_certification(self):
        digest = self.stage_and_activate()
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        state = observe_local_update(self.root)
        after = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        self.assertEqual(state, LocalUpdateObservation("LOCAL_RECEIPT_ONLY", "v2", digest))
        self.assertEqual(before, after)

    def test_pending_switch_never_appears_ready(self):
        digest = self.stage_and_activate()
        pointer = {
            "schema": "atlas-active-release/v1",
            "release": "v2", "archive_sha256": digest,
        }
        pending = {
            "schema": "atlas-release-switch/v1",
            "previous": pointer, "target": pointer,
        }
        (self.root / ".atlas-switch.pending.json").write_text(json.dumps(pending))
        observed = observe_local_update(self.root)
        self.assertEqual(observed.status, "RECOVERY_REQUIRED")
        self.assertIsNone(observed.release)

    def test_corrupt_or_missing_receipt_is_unverified(self):
        self.stage_and_activate()
        marker = self.root / "versions" / "v2" / ".atlas-stage.json"
        marker.write_text('{"schema":"tampered"}')
        self.assertEqual(observe_local_update(self.root).status, "UNVERIFIED_LOCAL_STATE")
        marker.unlink()
        self.assertEqual(observe_local_update(self.root).status, "UNVERIFIED_LOCAL_STATE")

    def test_corrupt_pointer_fails_closed(self):
        self.stage_and_activate()
        (self.root / "active-release.json").write_text('{"bad":true}')
        self.assertEqual(observe_local_update(self.root).status, "UNVERIFIED_LOCAL_STATE")


if __name__ == "__main__":
    unittest.main()

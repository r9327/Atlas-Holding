"""Transactional release pointer tests with real staged ZIPs and no process launch."""
import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import zipfile

from holding.update_stage import stage_release_archive
from holding.update_switch import (
    ReleaseSwitchError, activate_staged_release,
    read_active_release, recover_interrupted_switch,
)


class ReleaseSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "install"
        self.zip_path = Path(self.tmp.name) / "package.zip"
        self.digests = {}

    def stage(self, release):
        with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_STORED) as bundle:
            bundle.writestr("bin/AtlasHolding.exe", ("binary-" + release).encode())
        digest = sha256(self.zip_path.read_bytes()).hexdigest()
        stage_release_archive(
            self.zip_path, self.root, release=release, expected_sha256=digest,
        )
        self.digests[release] = digest
        return digest

    def activate(self, release, probe=None):
        return activate_staged_release(
            self.root, release=release,
            approved_archive_sha256=self.digests[release],
            health_probe=probe or (lambda _: True),
        )

    def state(self, release):
        return {
            "schema": "atlas-active-release/v1",
            "release": release,
            "archive_sha256": self.digests[release],
        }

    def set_pending(self, previous, target):
        doc = {
            "schema": "atlas-release-switch/v1",
            "previous": None if previous is None else self.state(previous),
            "target": self.state(target),
        }
        (self.root / ".atlas-switch.pending.json").write_text(
            json.dumps(doc), encoding="utf-8",
        )

    def test_activate_verified_slot_only_changes_pointer(self):
        digest = self.stage("v1")
        observed = []
        def probe(folder):
            observed.append(folder)
            self.assertEqual(read_active_release(self.root).release, "v1")
            return True
        result = self.activate("v1", probe=probe)
        self.assertEqual((result.status, result.previous_release), ("ACTIVATED", None))
        self.assertEqual(read_active_release(self.root).archive_sha256, digest)
        self.assertEqual(len(observed), 1)
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())
        self.assertEqual((self.root / "versions" / "v1" / "bin" / "AtlasHolding.exe").read_bytes(), b"binary-v1")

    def test_healthcheck_failure_rolls_back_to_previous_version(self):
        self.stage("v1")
        self.activate("v1")
        self.stage("v2")
        with self.assertRaisesRegex(ReleaseSwitchError, "restored"):
            self.activate("v2", probe=lambda _: False)
        self.assertEqual(read_active_release(self.root).release, "v1")
        self.assertTrue((self.root / "versions" / "v2").exists())
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_initial_failed_activation_removes_pointer(self):
        self.stage("v1")
        with self.assertRaisesRegex(ReleaseSwitchError, "restored"):
            self.activate("v1", probe=lambda _: False)
        self.assertIsNone(read_active_release(self.root))

    def test_exception_in_probe_rolls_back_and_never_deletes_slot(self):
        self.stage("v1")
        def broken(_):
            raise RuntimeError("runtime start refused")
        with self.assertRaisesRegex(ReleaseSwitchError, "restored"):
            self.activate("v1", probe=broken)
        self.assertIsNone(read_active_release(self.root))
        self.assertTrue((self.root / "versions" / "v1").exists())

    def test_already_active_does_not_run_probe_or_mutate_pointer(self):
        self.stage("v1")
        self.activate("v1")
        before = (self.root / "active-release.json").read_bytes()
        result = self.activate("v1", probe=lambda _: self.fail("probe called"))
        self.assertEqual(result.status, "ALREADY_ACTIVE")
        self.assertEqual(before, (self.root / "active-release.json").read_bytes())

    def test_interrupted_switch_recovers_previous_without_executing_code(self):
        self.stage("v1")
        self.activate("v1")
        self.stage("v2")
        self.set_pending("v1", "v2")
        (self.root / "active-release.json").write_text(json.dumps(self.state("v2")))
        self.assertTrue(recover_interrupted_switch(self.root))
        self.assertEqual(read_active_release(self.root).release, "v1")
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())
        self.assertFalse(recover_interrupted_switch(self.root))

    def test_pending_before_pointer_change_is_resolved_without_mutation(self):
        self.stage("v1")
        self.activate("v1")
        self.stage("v2")
        self.set_pending("v1", "v2")
        before = (self.root / "active-release.json").read_bytes()
        self.assertTrue(recover_interrupted_switch(self.root))
        self.assertEqual(before, (self.root / "active-release.json").read_bytes())

    def test_conflicting_active_pointer_fails_closed_during_recovery(self):
        self.stage("v1")
        self.activate("v1")
        self.stage("v2")
        self.set_pending("v1", "v2")
        (self.root / "active-release.json").write_text(json.dumps(self.state("v1") | {"release": "v3"}))
        with self.assertRaisesRegex(ReleaseSwitchError, "conflicts"):
            recover_interrupted_switch(self.root)
        self.assertTrue((self.root / ".atlas-switch.pending.json").exists())

    def test_unresolved_pending_blocks_another_activation(self):
        self.stage("v1")
        self.set_pending(None, "v1")
        with self.assertRaisesRegex(ReleaseSwitchError, "recovered first"):
            self.activate("v1")

    def test_wrong_manifest_hash_never_changes_active_pointer(self):
        self.stage("v1")
        with self.assertRaisesRegex(ReleaseSwitchError, "receipt"):
            activate_staged_release(
                self.root, release="v1",
                approved_archive_sha256="f" * 64,
                health_probe=lambda _: True,
            )
        self.assertIsNone(read_active_release(self.root))
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_tampered_receipt_rejected_before_transaction(self):
        self.stage("v1")
        marker = self.root / "versions" / "v1" / ".atlas-stage.json"
        marker.write_text('{"schema":"fake"}')
        with self.assertRaisesRegex(ReleaseSwitchError, "receipt"):
            self.activate("v1")
        self.assertIsNone(read_active_release(self.root))

    def test_bad_active_pointer_fails_closed(self):
        self.stage("v1")
        (self.root / "active-release.json").write_text(
            '{"schema":"atlas-active-release/v1","release":"v0","release":"v1","archive_sha256":"' +
            self.digests["v1"] + '"}', encoding="utf-8",
        )
        with self.assertRaises(ReleaseSwitchError):
            self.activate("v1")
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())


if __name__ == "__main__":
    unittest.main()

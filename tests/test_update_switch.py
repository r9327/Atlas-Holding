"""Transactional release pointer tests with real staged ZIPs and no process launch."""
import json
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from holding.dashboard_ipc import DashboardIpcClient, DashboardIpcServer
from holding.state_store import StateStore
from holding.update_health import (
    UpdateEngineSession, activate_staged_release_after_authenticated_ipc,
)
from holding.update_lock import UpdateLockError, exclusive_update_lock
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

    def test_authenticated_ipc_health_activates_selected_slot(self):
        digest = self.stage("v1")
        database = Path(self.tmp.name) / "engine.sqlite"
        with StateStore(database):
            pass
        key = b"r" * 32
        engine_id = "updater-health-v1"
        observed = []
        close = Mock()
        with DashboardIpcServer.for_database(
                database, engine_id=engine_id, secret=key) as server:
            activation = activate_staged_release_after_authenticated_ipc(
                self.root,
                release="v1",
                approved_archive_sha256=digest,
                session_factory=lambda slot: (
                    observed.append(slot) or UpdateEngineSession(
                        DashboardIpcClient(
                            port=server.port, secret=key,
                            engine_id=engine_id,
                        ),
                        close,
                    )
                ),
            )
        self.assertEqual(activation.switch.status, "ACTIVATED")
        self.assertIs(activation.session.close, close)
        close.assert_not_called()
        self.assertEqual(observed, [self.root / "versions" / "v1"])
        self.assertEqual(read_active_release(self.root).release, "v1")
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_unauthenticated_ipc_health_restores_previous_slot(self):
        self.stage("v1")
        self.activate("v1")
        digest = self.stage("v2")
        database = Path(self.tmp.name) / "engine.sqlite"
        with StateStore(database):
            pass
        engine_id = "updater-health-v2"
        close = Mock()
        with DashboardIpcServer.for_database(
                database, engine_id=engine_id, secret=b"r" * 32) as server:
            with self.assertRaisesRegex(ReleaseSwitchError, "restored"):
                activate_staged_release_after_authenticated_ipc(
                    self.root,
                    release="v2",
                    approved_archive_sha256=digest,
                    session_factory=lambda _slot: UpdateEngineSession(
                        DashboardIpcClient(
                            port=server.port, secret=b"w" * 32,
                            engine_id=engine_id,
                        ),
                        close,
                    ),
                )
        close.assert_called_once_with()
        self.assertEqual(read_active_release(self.root).release, "v1")
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_wrong_engine_identity_restores_previous_slot(self):
        self.stage("v1")
        self.activate("v1")
        digest = self.stage("v2")
        database = Path(self.tmp.name) / "engine.sqlite"
        with StateStore(database):
            pass
        key = b"r" * 32
        close = Mock()
        with DashboardIpcServer.for_database(
                database, engine_id="actual-engine", secret=key) as server:
            with self.assertRaisesRegex(ReleaseSwitchError, "restored"):
                activate_staged_release_after_authenticated_ipc(
                    self.root,
                    release="v2",
                    approved_archive_sha256=digest,
                    session_factory=lambda _slot: UpdateEngineSession(
                        DashboardIpcClient(
                            port=server.port, secret=key,
                            engine_id="different-engine",
                        ),
                        close,
                    ),
                )
        close.assert_called_once_with()
        self.assertEqual(read_active_release(self.root).release, "v1")
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_boolean_health_cannot_satisfy_authenticated_ipc_gate(self):
        digest = self.stage("v1")
        with self.assertRaisesRegex(ReleaseSwitchError, "restored"):
            activate_staged_release_after_authenticated_ipc(
                self.root,
                release="v1",
                approved_archive_sha256=digest,
                session_factory=lambda _slot: True,
            )
        self.assertIsNone(read_active_release(self.root))
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_failed_candidate_cleanup_is_never_hidden(self):
        digest = self.stage("v1")
        database = Path(self.tmp.name) / "engine.sqlite"
        with StateStore(database):
            pass
        key = b"r" * 32
        close = Mock(side_effect=OSError("denied"))
        with DashboardIpcServer.for_database(
                database, engine_id="actual-engine", secret=key) as server:
            with self.assertRaisesRegex(
                    ReleaseSwitchError, "cleanup failed"):
                activate_staged_release_after_authenticated_ipc(
                    self.root,
                    release="v1",
                    approved_archive_sha256=digest,
                    session_factory=lambda _slot: UpdateEngineSession(
                        DashboardIpcClient(
                            port=server.port, secret=key,
                            engine_id="different-engine",
                        ),
                        close,
                    ),
                )
        close.assert_called_once_with()
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

    def test_exclusive_lock_blocks_second_process_and_then_releases(self):
        self.stage("v1")
        child_code = """
from pathlib import Path
import sys
from holding.update_lock import UpdateLockError, exclusive_update_lock
try:
    with exclusive_update_lock(Path(sys.argv[1])):
        print("ACQUIRED", flush=True)
except UpdateLockError:
    print("BUSY", flush=True)
"""
        with exclusive_update_lock(self.root):
            blocked = subprocess.run(
                [sys.executable, "-c", child_code, str(self.root)],
                cwd=Path(__file__).parents[1], text=True,
                capture_output=True, timeout=10, check=True,
            )
        acquired = subprocess.run(
            [sys.executable, "-c", child_code, str(self.root)],
            cwd=Path(__file__).parents[1], text=True,
            capture_output=True, timeout=10, check=True,
        )
        self.assertEqual(blocked.stdout.strip(), "BUSY")
        self.assertEqual(acquired.stdout.strip(), "ACQUIRED")

    def test_activation_refuses_while_another_process_holds_lock(self):
        self.stage("v1")
        child_code = """
from pathlib import Path
import sys
from holding.update_lock import exclusive_update_lock
with exclusive_update_lock(Path(sys.argv[1])):
    print("READY", flush=True)
    sys.stdin.readline()
"""
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, str(self.root)],
            cwd=Path(__file__).parents[1], text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.assertEqual(child.stdout.readline().strip(), "READY")
        with self.assertRaisesRegex(ReleaseSwitchError, "another update operation"):
            self.activate("v1")
        self.assertIsNone(read_active_release(self.root))
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=10), 0)
        child.stdin.close()
        child.stdout.close()

    def test_symlinked_lock_file_is_refused(self):
        self.root.mkdir(parents=True)
        target = Path(self.tmp.name) / "outside.lock"
        target.write_bytes(b"x")
        try:
            (self.root / ".atlas-update.lock").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("filesystem symlinks unavailable")
        with self.assertRaisesRegex(UpdateLockError, "unsafe"):
            with exclusive_update_lock(self.root):
                self.fail("unsafe lock acquired")

    def test_non_regular_lock_file_is_refused(self):
        self.root.mkdir(parents=True)
        (self.root / ".atlas-update.lock").mkdir()
        with self.assertRaisesRegex(UpdateLockError, "safely|unsafe"):
            with exclusive_update_lock(self.root):
                self.fail("unsafe lock acquired")


if __name__ == "__main__":
    unittest.main()

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
from holding.update_stage import ReleaseStagingError, stage_release_archive
from holding.update_runtime import EngineReleasePins, stage_windows_engine_release
from holding.update_switch import (
    ReleaseSwitchError, activate_staged_release,
    read_active_release, recover_interrupted_switch,
)
from holding.windows_runtime_launch import (
    InstalledEngineSpec, _read_config, _validate_paths,
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

    def test_same_size_content_corruption_rejected_before_transaction(self):
        self.stage("v1")
        executable = self.root / "versions" / "v1" / "bin" / "AtlasHolding.exe"
        executable.write_bytes(b"corrupt!!")
        with self.assertRaisesRegex(ReleaseSwitchError, "content"):
            self.activate("v1")
        self.assertIsNone(read_active_release(self.root))
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_extra_or_missing_slot_file_rejected_before_transaction(self):
        for mutation in ("extra", "missing"):
            with self.subTest(mutation=mutation):
                release = "v-" + mutation
                self.stage(release)
                folder = self.root / "versions" / release
                if mutation == "extra":
                    (folder / "injected.dll").write_bytes(b"payload")
                else:
                    (folder / "bin" / "AtlasHolding.exe").unlink()
                with self.assertRaisesRegex(ReleaseSwitchError, "content|integrity"):
                    self.activate(release)
                self.assertIsNone(read_active_release(self.root))
                self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_external_hard_link_rejected_before_transaction(self):
        self.stage("v1")
        executable = self.root / "versions" / "v1" / "bin" / "AtlasHolding.exe"
        outside = Path(self.tmp.name) / "outside-hardlink.exe"
        try:
            outside.hardlink_to(executable)
        except OSError:
            self.skipTest("filesystem hard links unavailable")
        with self.assertRaisesRegex(ReleaseSwitchError, "integrity"):
            self.activate("v1")
        self.assertIsNone(read_active_release(self.root))
        self.assertFalse((self.root / ".atlas-switch.pending.json").exists())

    def test_materialized_engine_slot_binds_runtime_and_local_config(self):
        self.root.mkdir()
        old_runtime = self.root / "runtime" / "old-pair"
        old_runtime.mkdir(parents=True)
        old_viewer = old_runtime / "AtlasHoldingControlCenter.exe"
        old_engine = old_runtime / "AtlasHoldingEngine.exe"
        old_viewer.write_bytes(b"v" * 1_000_001)
        old_engine.write_bytes(b"e" * 1_000_003)
        old_viewer_hash = sha256(old_viewer.read_bytes()).hexdigest()
        old_engine_hash = sha256(old_engine.read_bytes()).hexdigest()
        config = self.root / "config" / "engine.json"
        config.parent.mkdir()
        config.write_text(json.dumps({
            "schema": 3,
            "dashboard": {
                "executable": str(old_viewer),
                "executable_sha256": old_viewer_hash,
                "script": None, "script_sha256": None, "readonly": False,
            },
        }), encoding="utf-8")
        current = InstalledEngineSpec(
            self.root, old_runtime, config, old_viewer_hash, old_engine_hash,
            sha256(config.read_bytes()).hexdigest(),
        )
        new_viewer = b"V" * 1_000_011
        new_engine = b"E" * 1_000_013
        viewer_hash = sha256(new_viewer).hexdigest()
        engine_hash = sha256(new_engine).hexdigest()
        pair = viewer_hash[:16] + "-" + engine_hash[:16]
        with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_STORED) as bundle:
            bundle.writestr(
                "runtime/" + pair + "/AtlasHoldingControlCenter.exe",
                new_viewer,
            )
            bundle.writestr(
                "runtime/" + pair + "/AtlasHoldingEngine.exe",
                new_engine,
            )
        archive_hash = sha256(self.zip_path.read_bytes()).hexdigest()
        pins = EngineReleasePins(
            "v2", archive_hash, viewer_hash, engine_hash,
        )
        result = stage_windows_engine_release(
            self.zip_path, self.root, pins=pins, current=current,
        )
        _validate_paths(result.launch_spec)
        _read_config(result.launch_spec)
        self.assertEqual(result.staged.files, 3)
        self.assertEqual(result.launch_spec.release_directory,
                         self.root / "versions" / "v2")
        generated = json.loads(result.launch_spec.config_file.read_text())
        self.assertEqual(
            generated["dashboard"]["executable"],
            str(result.launch_spec.runtime_directory /
                "AtlasHoldingControlCenter.exe"),
        )
        self.assertEqual(generated["dashboard"]["executable_sha256"],
                         viewer_hash)
        self.digests["v2"] = archive_hash
        activated = self.activate("v2")
        self.assertEqual(activated.status, "ACTIVATED")

    def test_engine_archive_cannot_supply_install_specific_config(self):
        self.root.mkdir()
        runtime = self.root / "runtime" / "old"
        runtime.mkdir(parents=True)
        viewer = runtime / "AtlasHoldingControlCenter.exe"
        engine = runtime / "AtlasHoldingEngine.exe"
        viewer.write_bytes(b"v" * 1_000_001)
        engine.write_bytes(b"e" * 1_000_003)
        viewer_hash = sha256(viewer.read_bytes()).hexdigest()
        engine_hash = sha256(engine.read_bytes()).hexdigest()
        config = self.root / "config" / "engine.json"
        config.parent.mkdir()
        config.write_text(json.dumps({
            "schema": 3,
            "dashboard": {
                "executable": str(viewer),
                "executable_sha256": viewer_hash,
                "script": None, "script_sha256": None, "readonly": False,
            },
        }), encoding="utf-8")
        current = InstalledEngineSpec(
            self.root, runtime, config, viewer_hash, engine_hash,
            sha256(config.read_bytes()).hexdigest(),
        )
        pair = viewer_hash[:16] + "-" + engine_hash[:16]
        with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_STORED) as bundle:
            bundle.writestr("runtime/" + pair + "/AtlasHoldingControlCenter.exe",
                            viewer.read_bytes())
            bundle.writestr("runtime/" + pair + "/AtlasHoldingEngine.exe",
                            engine.read_bytes())
            bundle.writestr("config/engine.json", b"{}")
        pins = EngineReleasePins(
            "v2", sha256(self.zip_path.read_bytes()).hexdigest(),
            viewer_hash, engine_hash,
        )
        with self.assertRaisesRegex(ReleaseStagingError, "must not provide"):
            stage_windows_engine_release(
                self.zip_path, self.root, pins=pins, current=current,
            )
        self.assertFalse((self.root / "versions" / "v2").exists())

    def test_stale_source_config_or_wrong_runtime_pin_publishes_nothing(self):
        self.root.mkdir()
        runtime = self.root / "runtime" / "old"
        runtime.mkdir(parents=True)
        viewer = runtime / "AtlasHoldingControlCenter.exe"
        engine = runtime / "AtlasHoldingEngine.exe"
        viewer.write_bytes(b"v" * 1_000_001)
        engine.write_bytes(b"e" * 1_000_003)
        viewer_hash = sha256(viewer.read_bytes()).hexdigest()
        engine_hash = sha256(engine.read_bytes()).hexdigest()
        config = self.root / "config" / "engine.json"
        config.parent.mkdir()
        config.write_text(json.dumps({
            "schema": 3,
            "dashboard": {
                "executable": str(viewer),
                "executable_sha256": viewer_hash,
                "script": None, "script_sha256": None, "readonly": False,
            },
        }), encoding="utf-8")
        current = InstalledEngineSpec(
            self.root, runtime, config, viewer_hash, engine_hash,
            sha256(config.read_bytes()).hexdigest(),
        )
        pair = viewer_hash[:16] + "-" + engine_hash[:16]
        with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_STORED) as bundle:
            bundle.writestr("runtime/" + pair + "/AtlasHoldingControlCenter.exe",
                            viewer.read_bytes())
            bundle.writestr("runtime/" + pair + "/AtlasHoldingEngine.exe",
                            engine.read_bytes())
        archive_hash = sha256(self.zip_path.read_bytes()).hexdigest()
        config.write_text('{"schema":3}', encoding="utf-8")
        with self.assertRaisesRegex(ReleaseStagingError, "cannot seed"):
            stage_windows_engine_release(
                self.zip_path, self.root,
                pins=EngineReleasePins(
                    "v2", archive_hash, viewer_hash, engine_hash,
                ),
                current=current,
            )
        config.write_text(json.dumps({
            "schema": 3,
            "dashboard": {
                "executable": str(viewer),
                "executable_sha256": viewer_hash,
                "script": None, "script_sha256": None, "readonly": False,
            },
        }), encoding="utf-8")
        current = InstalledEngineSpec(
            self.root, runtime, config, viewer_hash, engine_hash,
            sha256(config.read_bytes()).hexdigest(),
        )
        with self.assertRaisesRegex(ReleaseStagingError, "layout"):
            stage_windows_engine_release(
                self.zip_path, self.root,
                pins=EngineReleasePins(
                    "v2", archive_hash, "0" * 64, engine_hash,
                ),
                current=current,
            )
        self.assertFalse((self.root / "versions" / "v2").exists())

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

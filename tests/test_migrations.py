from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from holding.autonomy import Mission
from holding.daemon_lease import DaemonLease
from holding.migrations import CURRENT_SCHEMA, SchemaMigrator
from holding.schema import (
    CORE_SCHEMA, MODEL_SCHEMA_V1, RUNTIME_SCHEMA_V2, RUNTIME_SCHEMA_V3,
    apply_schema,
)
from holding.sqlite_backup import restore_snapshot
from holding.state_store import StateStore, StoreError

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=StateStore(self.root/'holding.sqlite');self.addCleanup(self.store.close)
        self.lease=DaemonLease(self.store.path,heartbeat=False)
        self.lease.acquire();self.addCleanup(self.lease.release)
        self.migrator=SchemaMigrator(self.store,backup_dir=self.root/'backups')

    def test_migrates_with_verified_restorable_backup(self):
        self.store.add_missions([Mission('one','r9327/Dof-Atlas')])
        upgrade=self.migrator.ensure(lease_owner=self.lease.owner)
        self.assertEqual((upgrade.from_version,upgrade.to_version),(0,CURRENT_SCHEMA))
        self.assertIsNotNone(upgrade.backup)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0],CURRENT_SCHEMA)
        restore=restore_snapshot(upgrade.backup.database,self.root/'restored.sqlite')
        with closing(sqlite3.connect(restore)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT id FROM missions").fetchone()[0],'one')

    def test_repeated_startup_does_not_repeat_migrations_or_backups(self):
        first=self.migrator.ensure(lease_owner=self.lease.owner)
        second=self.migrator.ensure(lease_owner=self.lease.owner)
        self.assertEqual(second.backup,None)
        self.assertEqual(len(list((self.root/'backups').iterdir())),2)

    def test_v1_database_upgrades_to_complete_runtime_schema(self):
        for statement in MODEL_SCHEMA_V1:
            self.store.db.execute(statement)
        self.store.db.execute('PRAGMA user_version=1')

        upgrade=self.migrator.ensure(lease_owner=self.lease.owner)

        self.assertEqual((upgrade.from_version,upgrade.to_version),(1,CURRENT_SCHEMA))
        tables={row[0] for row in self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({
            'daemon_lease','git_operations','signed_proofs','signed_proof_history',
            'scoped_qa_advisories','scoped_qa_retries','qa_repair_attempts',
            'roadmap_versions','mission_specs','roadmap_amendments',
            'external_service_health','github_sync_attempts',
            'github_sync_context',
        }.issubset(tables))

    def test_reopened_v1_database_preserves_legacy_rows(self):
        path = self.root / 'legacy-v1.sqlite'
        with closing(sqlite3.connect(path)) as legacy:
            apply_schema(legacy, CORE_SCHEMA + MODEL_SCHEMA_V1)
            legacy.execute("""INSERT INTO missions(
                id,repo,operation,dependencies,priority,state,candidate_sha,evidence_sha,
                external_required,product_decision_required,failures_without_progress,updated_at)
                VALUES('legacy','r9327/Dof-Atlas','CODE','[]',50,'READY','','',0,0,0,1)""")
            legacy.execute("""INSERT INTO model_profiles VALUES(
                ?,?,?,?,?,?,?,?,?)""", ('a' * 64, 'legacy-model', 'Ollama', 'Q4',
                                         4096, 100, 20, 'b' * 64, 1))
            legacy.execute('PRAGMA user_version=1')
            legacy.commit()

        old = StateStore(path)
        lease = DaemonLease(path, heartbeat=False)
        lease.acquire()
        try:
            result = SchemaMigrator(old, backup_dir=self.root / 'legacy-backups').ensure(
                lease_owner=lease.owner)
            self.assertEqual((result.from_version, result.to_version), (1, CURRENT_SCHEMA))
            self.assertEqual(old.db.execute(
                "SELECT repo FROM missions WHERE id='legacy'").fetchone()[0],
                'r9327/Dof-Atlas')
            self.assertEqual(old.db.execute(
                "SELECT model_name FROM model_profiles WHERE digest_sha256=?",
                ('a' * 64,)).fetchone()[0], 'legacy-model')
            self.assertTrue(old.integrity_check())
        finally:
            lease.release()
            old.close()

    def test_v2_database_upgrades_sync_state_with_backup(self):
        apply_schema(self.store.db, MODEL_SCHEMA_V1 + RUNTIME_SCHEMA_V2)
        self.store.db.execute('PRAGMA user_version=2')

        result = self.migrator.ensure(lease_owner=self.lease.owner)

        self.assertEqual((result.from_version, result.to_version), (2, CURRENT_SCHEMA))
        self.assertIsNotNone(result.backup)
        tables = {row[0] for row in self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'external_service_health', 'github_sync_attempts'}.issubset(tables))

    def test_v3_database_upgrades_recovery_context_with_backup(self):
        apply_schema(
            self.store.db,
            MODEL_SCHEMA_V1 + RUNTIME_SCHEMA_V2 + RUNTIME_SCHEMA_V3,
        )
        self.store.db.execute('PRAGMA user_version=3')

        result = self.migrator.ensure(lease_owner=self.lease.owner)

        self.assertEqual((result.from_version, result.to_version), (3, CURRENT_SCHEMA))
        self.assertIsNotNone(result.backup)
        self.assertIsNotNone(self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='github_sync_context'"
        ).fetchone())

    def test_invalid_source_integrity_refuses_backup_and_mutation(self):
        with patch.object(self.store, 'integrity_check', return_value=False):
            with self.assertRaisesRegex(StoreError, 'integrity check failed'):
                self.migrator.ensure(lease_owner=self.lease.owner)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 0)
        self.assertFalse((self.root / 'backups').exists())

    def test_failed_post_migration_integrity_rolls_back_schema_version(self):
        with patch.object(self.store, 'integrity_check', side_effect=(True, False)):
            with self.assertRaisesRegex(StoreError, 'integrity check failed'):
                self.migrator.ensure(lease_owner=self.lease.owner)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 0)
        self.assertFalse(self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_profiles'"
        ).fetchone())

    def test_malformed_database_is_refused_before_startup_schema_writes(self):
        path = self.root / 'malformed.sqlite'
        seed = StateStore(path)
        seed.close()
        with closing(sqlite3.connect(path)) as raw:
            raw.execute('PRAGMA writable_schema=ON')
            raw.execute(
                "UPDATE sqlite_master SET sql='CREATE TABLE missions(' "
                "WHERE type='table' AND name='missions'"
            )
            raw.execute('PRAGMA writable_schema=OFF')
            raw.execute('PRAGMA schema_version=999')
            raw.commit()

        with self.assertRaisesRegex(StoreError, 'invalid or corrupt'):
            StateStore(path)

    def test_newer_schema_refused_without_downgrade(self):
        self.store.db.execute('PRAGMA user_version=99')
        with self.assertRaises(StoreError):self.migrator.ensure(lease_owner=self.lease.owner)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0],99)

    def test_model_metadata_tracks_measured_resources_and_role(self):
        self.store.add_missions([Mission('one','r9327/Dof-Atlas')])
        self.migrator.ensure(lease_owner=self.lease.owner)
        self.migrator.register_model(lease_owner=self.lease.owner,digest_sha256='a'*64,
             name='local model',provider='Ollama',quantization='Q4',context_measured=8192,
             ram_peak_bytes=1048576,vram_peak_bytes=2048,benchmark_digest='b'*64)
        self.migrator.record_use(lease_owner=self.lease.owner,mission_id='one',role='coder',
                                  model_digest_sha256='a'*64)
        self.assertEqual(self.store.db.execute('SELECT context_measured FROM model_profiles').fetchone()[0],8192)
        self.assertEqual(self.store.db.execute('SELECT role FROM mission_model_usage').fetchone()[0],'coder')

    def test_fake_profile_without_benchmark_digest_is_refused(self):
        self.migrator.ensure(lease_owner=self.lease.owner)
        with self.assertRaises(StoreError):
            self.migrator.register_model(lease_owner=self.lease.owner,digest_sha256='a'*64,
                name='claimed',provider='Ollama',quantization='Q4',context_measured=8192,
                ram_peak_bytes=1,vram_peak_bytes=2,benchmark_digest='invented')

    def test_stale_daemon_cannot_upgrade(self):
        self.lease.release()
        with self.assertRaises(StoreError):self.migrator.ensure(lease_owner=self.lease.owner)

if __name__=='__main__':unittest.main()

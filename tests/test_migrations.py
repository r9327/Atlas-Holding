from pathlib import Path
import sqlite3
import tempfile
import unittest

from holding.autonomy import Mission
from holding.daemon_lease import DaemonLease
from holding.migrations import SchemaMigrator
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
        self.assertEqual((upgrade.from_version,upgrade.to_version),(0,1))
        self.assertIsNotNone(upgrade.backup)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0],1)
        restore=restore_snapshot(upgrade.backup.database,self.root/'restored.sqlite')
        with sqlite3.connect(restore) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT id FROM missions").fetchone()[0],'one')

    def test_repeated_startup_does_not_repeat_migrations_or_backups(self):
        first=self.migrator.ensure(lease_owner=self.lease.owner)
        second=self.migrator.ensure(lease_owner=self.lease.owner)
        self.assertEqual(second.backup,None)
        self.assertEqual(len(list((self.root/'backups').iterdir())),2)

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

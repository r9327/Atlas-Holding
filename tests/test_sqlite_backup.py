from contextlib import closing
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

from holding.sqlite_backup import create_snapshot, restore_snapshot, BackupError


class SQLiteBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "state.sqlite"
        self.db = sqlite3.connect(self.source)
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE checkpoints(id INTEGER PRIMARY KEY, payload TEXT)")
        self.db.execute("INSERT INTO checkpoints(payload) VALUES('after-crash')")
        self.db.commit()

    def snapshot(self):
        return create_snapshot(self.db, self.root / "backup.sqlite")

    def test_restore_recovers_wal_data(self):
        backup = self.snapshot()
        self.assertEqual(len(backup.digest), 64)
        restored = restore_snapshot(backup.database, self.root / "restored.sqlite")
        with closing(sqlite3.connect(restored)) as db:
            self.assertEqual(db.execute("SELECT payload FROM checkpoints").fetchone()[0], "after-crash")
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_snapshot_does_not_leave_temporary_wal_or_shm(self):
        self.snapshot()
        children = sorted(p.name for p in self.root.iterdir() if p.name.startswith('.atlas-snapshot-'))
        self.assertEqual(children, [])

    def test_immutable_snapshot(self):
        self.snapshot()
        with self.assertRaises(BackupError):
            self.snapshot()

    def test_existing_database_not_overwritten(self):
        backup = self.snapshot()
        with self.assertRaises(BackupError):
            restore_snapshot(backup.database, self.source)

    def test_tampering_detected(self):
        backup = self.snapshot()
        with backup.database.open("ab") as f:
            f.write(b"tampered")
        destination = self.root / "restored.sqlite"
        with self.assertRaises(BackupError):
            restore_snapshot(backup.database, destination)
        self.assertFalse(destination.exists())

    def test_manifest_required(self):
        backup = self.snapshot()
        backup.database.with_suffix(".sqlite.sha256").unlink()
        with self.assertRaises(BackupError):
            restore_snapshot(backup.database, self.root / "restored.sqlite")

    def test_snapshot_race_never_overwrites_user_file(self):
        target = self.root / "backup.sqlite"
        def competing_writer(_temporary, published):
            Path(published).write_text("USER-DATA", encoding="ascii")
            raise FileExistsError("simulated competing writer")
        with patch("holding.sqlite_backup.os.link", side_effect=competing_writer):
            with self.assertRaises(BackupError):
                create_snapshot(self.db, target)
        self.assertEqual(target.read_text(encoding="ascii"), "USER-DATA")
        self.assertFalse(target.with_suffix(".sqlite.sha256").exists())

    def test_existing_wal_prevents_restore(self):
        backup = self.snapshot()
        destination = self.root / "restored.sqlite"
        Path(str(destination) + "-wal").write_text("live")
        with self.assertRaises(BackupError):
            restore_snapshot(backup.database, destination)

    def test_manifest_symlink_denied(self):
        backup = self.snapshot()
        manifest = backup.database.with_suffix(".sqlite.sha256")
        redirect = self.root / "redirect"
        redirect.write_bytes(manifest.read_bytes())
        manifest.unlink()
        manifest.symlink_to(redirect)
        with self.assertRaises(BackupError):
            restore_snapshot(backup.database, self.root / "restored.sqlite")


if __name__ == "__main__":
    unittest.main()

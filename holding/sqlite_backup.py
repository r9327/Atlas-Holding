"""SQLite backup/restore with checksum and fail-closed destinations.

Backup uses sqlite3.Connection.backup to include committed WAL transactions.
Restoration NEVER overwrites an existing database or user file.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import sqlite3
import tempfile


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    database: Path
    digest: str
    bytes_written: int


def digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_snapshot(connection: sqlite3.Connection, target: str | Path) -> Snapshot:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = target.with_suffix(target.suffix + ".sha256")
    if target.exists() or target.is_symlink() or manifest.exists() or manifest.is_symlink():
        raise BackupError("Snapshot already exists")
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=".atlas-snapshot-", suffix=".db")
    os.close(fd)
    temporary = Path(name)
    published = False
    manifest_created = False
    try:
        with sqlite3.connect(temporary) as output:
            connection.backup(output)
            if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("SQLite snapshot integrity failed")
        digest = digest_file(temporary)
        count = temporary.stat().st_size
        if count == 0:
            raise BackupError("Empty snapshot")
        # An incomplete manifest+snapshot pair is never accepted by restore.
        with manifest.open("x", encoding="ascii") as file:
            manifest_created = True
            file.write(digest + "\n")
            file.flush()
            os.fsync(file.fileno())
        # Hard-link publication is atomic and refuses overwriting a file
        # created after preflight (unlike os.replace). Same-directory temp
        # ensures a same-filesystem operation.
        try:
            os.link(temporary, target)
        except OSError as exc:
            raise BackupError("Atomic no-overwrite snapshot publication failed") from exc
        published = True
        return Snapshot(target, digest, count)
    finally:
        temporary.unlink(missing_ok=True)
        if manifest_created and not published:
            manifest.unlink(missing_ok=True)


def restore_snapshot(snapshot: str | Path, destination: str | Path) -> Path:
    snapshot, destination = Path(snapshot), Path(destination)
    manifest = snapshot.with_suffix(snapshot.suffix + ".sha256")
    if (destination.exists() or destination.is_symlink()
            or Path(str(destination) + "-wal").exists()
            or Path(str(destination) + "-shm").exists()):
        raise BackupError("Restore destination must be unused")
    if (not snapshot.is_file() or snapshot.is_symlink()
            or not manifest.is_file() or manifest.is_symlink()):
        raise BackupError("Snapshot and manifest must be regular files")
    expected = manifest.read_text(encoding="ascii").strip()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise BackupError("Invalid checksum")
    if digest_file(snapshot) != expected:
        raise BackupError("Tampered snapshot")
    with sqlite3.connect(f"file:{snapshot.resolve()}?mode=ro", uri=True) as original:
        if original.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("SQLite snapshot corrupt")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusively reserve the filename to refuse races with other writers.
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with sqlite3.connect(destination) as restored:
                original.backup(restored)
                if restored.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("Restored database corrupt")
            if digest_file(destination) != expected:
                raise BackupError("Restored database differs from verified snapshot")
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return destination

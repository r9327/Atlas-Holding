"""Versioned core schema upgrades, with verified snapshot before mutation.

Call only from the trusted daemon while holding its exclusive lease. An upgrade
failure is fatal; it never silently continues with a partly upgraded database.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import secrets
import time

from .sqlite_backup import create_snapshot, Snapshot
from .state_store import StateStore, StoreError

CURRENT_SCHEMA = 1
SHA256 = re.compile(r'^[0-9a-f]{64}$')


@dataclass(frozen=True)
class Upgrade:
    from_version: int
    to_version: int
    backup: Snapshot | None


class SchemaMigrator:
    def __init__(self, store: StateStore, *, backup_dir: Path):
        self.store = store
        self.backup_dir = backup_dir

    def ensure(self, *, lease_owner: str) -> Upgrade:
        self.store._assert_daemon_fence(lease_owner)
        version = int(self.store.db.execute('PRAGMA user_version').fetchone()[0])
        if version > CURRENT_SCHEMA:
            raise StoreError('Refusing database created by newer Holding version')
        if version == CURRENT_SCHEMA:
            return Upgrade(version, version, None)
        if version != 0:
            raise StoreError('Unsupported database migration path')
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self.backup_dir / (
            f'{self.store.path.name}.before-v{CURRENT_SCHEMA}.{int(time.time())}.{secrets.token_hex(6)}.sqlite'
        )
        snapshot = create_snapshot(self.store.db, path)
        with self.store._transaction():
            self.store._assert_daemon_fence(lease_owner)
            if int(self.store.db.execute('PRAGMA user_version').fetchone()[0]) != 0:
                raise StoreError('Concurrent schema version change')
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS model_profiles (
                digest_sha256 TEXT PRIMARY KEY,
                model_name TEXT NOT NULL,
                provider TEXT NOT NULL,
                quantization TEXT NOT NULL,
                context_measured INTEGER NOT NULL CHECK(context_measured>0),
                ram_peak_bytes INTEGER NOT NULL CHECK(ram_peak_bytes>=0),
                vram_peak_bytes INTEGER NOT NULL CHECK(vram_peak_bytes>=0),
                benchmark_digest TEXT NOT NULL,
                observed_at INTEGER NOT NULL
            )''')
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS mission_model_usage (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL REFERENCES missions(id),
                role TEXT NOT NULL,
                model_digest_sha256 TEXT NOT NULL REFERENCES model_profiles(digest_sha256),
                candidate_sha TEXT NOT NULL,
                observed_at INTEGER NOT NULL
            )''')
            self.store.db.execute('PRAGMA user_version=1')
            self.store._event(None, 'SCHEMA_MIGRATION', f'0->1 backup_sha256:{snapshot.digest}')
        return Upgrade(0, CURRENT_SCHEMA, snapshot)

    def register_model(self, *, lease_owner: str, digest_sha256: str, name: str,
                       provider: str, quantization: str, context_measured: int,
                       ram_peak_bytes: int, vram_peak_bytes: int,
                       benchmark_digest: str) -> None:
        if (not SHA256.fullmatch(digest_sha256) or not SHA256.fullmatch(benchmark_digest) or
                not name or not provider or not quantization or
                any(type(v) is not int or v<0 for v in
                    (context_measured, ram_peak_bytes, vram_peak_bytes)) or
                context_measured < 1):
            raise StoreError('Only measured models with verifiable digests may be registered')
        with self.store._transaction():
            self.store._assert_daemon_fence(lease_owner)
            self.store.db.execute('''INSERT INTO model_profiles(
                digest_sha256,model_name,provider,quantization,context_measured,
                ram_peak_bytes,vram_peak_bytes,benchmark_digest,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(digest_sha256) DO NOTHING''',
                (digest_sha256,name,provider,quantization,context_measured,
                 ram_peak_bytes,vram_peak_bytes,benchmark_digest,int(time.time())))

    def record_use(self, *, lease_owner: str, mission_id: str, role: str,
                   model_digest_sha256: str, candidate_sha: str = '') -> None:
        if not role or not SHA256.fullmatch(model_digest_sha256):
            raise StoreError('Invalid model assignment')
        with self.store._transaction():
            self.store._assert_daemon_fence(lease_owner)
            self.store.db.execute('''INSERT INTO mission_model_usage(
                mission_id,role,model_digest_sha256,candidate_sha,observed_at)
                VALUES(?,?,?,?,?)''',
                (mission_id,role,model_digest_sha256,candidate_sha,int(time.time())))
            self.store._event(mission_id, 'MODEL_ROLE_USED', role + ':' + model_digest_sha256)

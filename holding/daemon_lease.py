"""Exclusive, time-limited SQLite daemon lease with crash takeover.

An expired owner is considered dead and its in-flight work MUST be quarantined
before a replacement daemon starts. Callers must recheck lease ownership before
committing any Git or SQLite checkpoint.
"""
from __future__ import annotations

from contextlib import contextmanager
import secrets
import sqlite3
import threading
import time
from pathlib import Path


class LeaseError(RuntimeError):
    pass


class DaemonLease:
    def __init__(self, db_path: str | Path, *, ttl_seconds: int = 60,
                 heartbeat: bool = True, owner: str | None = None):
        if not 10 <= ttl_seconds <= 600:
            raise ValueError("Lease ttl must be 10..600 seconds")
        self.path = Path(db_path)
        self.owner = owner or secrets.token_hex(24)
        self.ttl = ttl_seconds
        self.enable_heartbeat = heartbeat
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None
        self._held = False

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(str(self.path), timeout=8, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("""CREATE TABLE IF NOT EXISTS daemon_lease (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            owner TEXT NOT NULL,
            expires_epoch INTEGER NOT NULL
        )""")
        try:
            yield conn
            if conn.in_transaction:
                conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def acquire(self, *, now_epoch: int | None = None) -> str:
        if self._held:
            raise LeaseError("Lease already acquired")
        now = int(time.time()) if now_epoch is None else now_epoch
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner,expires_epoch FROM daemon_lease WHERE singleton=1").fetchone()
            if row is not None and row["expires_epoch"] > now:
                raise LeaseError("Another active daemon owns the work queue")
            takeover = "TAKEOVER" if row is not None else "NEW"
            db.execute("""INSERT INTO daemon_lease(singleton,owner,expires_epoch) VALUES(1,?,?)
                ON CONFLICT(singleton) DO UPDATE SET owner=excluded.owner,
                expires_epoch=excluded.expires_epoch""", (self.owner, now + self.ttl))
        self._held = True
        self._lost.clear()
        self._stop.clear()
        if self.enable_heartbeat:
            self._thread = threading.Thread(target=self._heartbeat, name="holding-daemon-lease", daemon=True)
            self._thread.start()
        return takeover

    def _heartbeat(self) -> None:
        while not self._stop.wait(max(1, self.ttl // 3)):
            try:
                self.renew()
            except (LeaseError, sqlite3.Error, OSError):
                self._lost.set()
                break

    def renew(self, *, now_epoch: int | None = None) -> None:
        if not self._held or self._lost.is_set():
            raise LeaseError("Lease is not held")
        now = int(time.time()) if now_epoch is None else now_epoch
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE daemon_lease SET expires_epoch=?
                WHERE singleton=1 AND owner=? AND expires_epoch>?""",
                (now + self.ttl, self.owner, now))
            if changed.rowcount != 1:
                self._lost.set()
                raise LeaseError("Daemon lease expired or stolen")

    def assert_owned(self, *, now_epoch: int | None = None) -> None:
        if not self._held or self._lost.is_set():
            raise LeaseError("No current daemon lease")
        now = int(time.time()) if now_epoch is None else now_epoch
        with self._db() as db:
            row = db.execute("SELECT owner,expires_epoch FROM daemon_lease WHERE singleton=1").fetchone()
        if row is None or row["owner"] != self.owner or row["expires_epoch"] <= now:
            self._lost.set()
            raise LeaseError("Daemon lease expired or stolen")

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        if self._held:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM daemon_lease WHERE singleton=1 AND owner=?", (self.owner,))
        self._held = False
        self._thread = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

"""Durable, bounded evidence of the internal MCP hub's observed lifecycle.

The trusted supervisor writes these observations. A separate read-only reader
may inspect them, but they are NOT HMAC-authenticated IPC proof until the engine
later carries them through its authenticated control channel. File protection
is a prerequisite for using this journal in any privileged decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

from .mcp_hub_session import McpSessionObservation
from .mcp_stdio import PROTOCOL_VERSION


_SHA = re.compile(r"^[0-9a-f]{64}$")
_STATES = frozenset({
    "NOT_STARTED", "UNVERIFIED", "RESPONSIVE", "STALE", "EXITED",
    "TIMEOUT", "OUTPUT_LIMIT", "BAD_PROTOCOL", "BINARY_CHANGED",
    "IO_ERROR", "CLOSED",
})
MAX_JOURNAL_EVENTS = 128
MAX_OBSERVATION_AGE = 30
_MAX_EPOCH = 4_102_444_800


class McpObservationError(ValueError):
    pass


@dataclass(frozen=True)
class DurableHubObservation:
    sequence: int
    state: str
    recorded_epoch: int
    pid: int | None
    source_sha256: str
    protocol_version: str | None
    provenance: str = "LOCAL_SUPERVISOR_OBSERVATION"


class HubObservationJournal:
    def __init__(self, database: Path, *, readonly: bool = False):
        if (not isinstance(database, Path) or not database.is_absolute()
                or type(readonly) is not bool or database.is_symlink()):
            raise McpObservationError("Trusted absolute journal path required")
        self.readonly = readonly
        if readonly:
            if not database.is_file():
                raise McpObservationError("Existing journal required for reader")
            self.db = sqlite3.connect(
                database.as_uri() + "?mode=ro", uri=True,
                isolation_level=None, timeout=5,
            )
        else:
            database.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(
                str(database), isolation_level=None, timeout=5,
            )
        try:
            self.db.execute("PRAGMA busy_timeout=5000")
            if readonly:
                self.db.execute("PRAGMA query_only=ON")
            else:
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("""
                    CREATE TABLE IF NOT EXISTS mcp_hub_observations (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        state TEXT NOT NULL,
                        recorded_epoch INTEGER NOT NULL,
                        pid INTEGER,
                        source_sha256 TEXT NOT NULL,
                        protocol_version TEXT
                    )
                """)
            columns = self.db.execute(
                "PRAGMA table_info(mcp_hub_observations)").fetchall()
            if [row[1] for row in columns] != [
                    "seq", "state", "recorded_epoch", "pid", "source_sha256",
                    "protocol_version"]:
                raise McpObservationError("Unsupported durable hub journal schema")
        except BaseException:
            self.db.close()
            raise

    def __enter__(self) -> "HubObservationJournal":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def record(self, observation: McpSessionObservation, *,
               source_sha256: str, now_epoch: int) -> int:
        """Only the trusted supervisor should record its own session result."""
        if self.readonly:
            raise McpObservationError("Read-only journal")
        if (type(observation) is not McpSessionObservation
                or observation.state not in _STATES
                or type(source_sha256) is not str or not _SHA.fullmatch(source_sha256)
                or type(now_epoch) is not int or not 0 <= now_epoch <= _MAX_EPOCH
                or type(observation.external_plugins_tested) is not bool
                or observation.external_plugins_tested
                or (observation.pid is not None
                    and (type(observation.pid) is not int
                         or not 1 <= observation.pid <= 2**32 - 1))):
            raise McpObservationError("Invalid trusted MCP session observation")
        if observation.state == "RESPONSIVE":
            if (observation.protocol_version != PROTOCOL_VERSION
                    or type(observation.last_verified_age_seconds)
                        not in (int, float)
                    or not 0 <= observation.last_verified_age_seconds <=
                        MAX_OBSERVATION_AGE
                    or observation.pid is None):
                raise McpObservationError("Unverified responsive hub claim")
        elif observation.protocol_version is not None:
            raise McpObservationError("Nonresponsive hub cannot claim protocol")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT recorded_epoch FROM mcp_hub_observations "
                "ORDER BY seq DESC LIMIT 1").fetchone()
            if row and now_epoch < row[0]:
                raise McpObservationError("Hub observations must be chronological")
            cursor = self.db.execute(
                "INSERT INTO mcp_hub_observations "
                "(state,recorded_epoch,pid,source_sha256,protocol_version) "
                "VALUES (?,?,?,?,?)",
                (observation.state, now_epoch, observation.pid,
                 source_sha256, observation.protocol_version),
            )
            self.db.execute(
                "DELETE FROM mcp_hub_observations WHERE seq NOT IN "
                "(SELECT seq FROM mcp_hub_observations ORDER BY seq DESC LIMIT ?)",
                (MAX_JOURNAL_EVENTS,),
            )
        return cursor.lastrowid

    def latest(self, *, now_epoch: int) -> DurableHubObservation | None:
        if type(now_epoch) is not int or not 0 <= now_epoch <= _MAX_EPOCH:
            raise McpObservationError("Invalid trusted journal clock")
        row = self.db.execute(
            "SELECT seq,state,recorded_epoch,pid,source_sha256,protocol_version "
            "FROM mcp_hub_observations ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            return None
        seq, state, recorded, pid, digest, protocol = row
        if (type(seq) is not int or seq < 1 or state not in _STATES
                or type(recorded) is not int or not 0 <= recorded <= _MAX_EPOCH
                or recorded > now_epoch
                or type(digest) is not str or not _SHA.fullmatch(digest)
                or (pid is not None
                    and (type(pid) is not int or not 1 <= pid <= 2**32 - 1))
                or (state == "RESPONSIVE") !=
                    (protocol == PROTOCOL_VERSION)):
            raise McpObservationError("Corrupted hub journal evidence")
        # A positive observation does not stay fresh indefinitely; other
        # states are persisted historically, never upgraded to RESPONSiVE.
        if state == "RESPONSIVE" and now_epoch - recorded > MAX_OBSERVATION_AGE:
            state, protocol = "STALE", None
        return DurableHubObservation(seq, state, recorded, pid, digest, protocol)

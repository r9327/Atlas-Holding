"""Durable MCP session evidence must never be mistaken for live plugins."""
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from holding.mcp_hub_journal import (
    HubObservationJournal, McpObservationError, MAX_JOURNAL_EVENTS,
)
from holding.mcp_hub_session import LocalMcpHubSession, McpSessionObservation
from holding.mcp_local_inventory import PinnedLocalPlugin
from holding.mcp_stdio import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000
DIGEST = "a" * 64


def observation(state="RESPONSIVE", pid=1234, age=0.0):
    return McpSessionObservation(
        state, pid, age,
        PROTOCOL_VERSION if state == "RESPONSIVE" else None)


class DurableMcpHubJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "hub.sqlite"
        self.writer = HubObservationJournal(self.db)
        self.addCleanup(self.writer.close)

    def test_real_observation_is_explicitly_historical_and_expires(self):
        seq = self.writer.record(
            observation(), source_sha256=DIGEST, now_epoch=NOW)
        self.assertEqual(seq, 1)
        with HubObservationJournal(self.db, readonly=True) as reader:
            recent = reader.latest(now_epoch=NOW + 2)
            self.assertEqual(recent.state, "RESPONSIVE")
            self.assertEqual(recent.protocol_version, PROTOCOL_VERSION)
            self.assertEqual(recent.source_sha256, DIGEST)
            self.assertEqual(recent.provenance, "LOCAL_SUPERVISOR_OBSERVATION")
            self.assertEqual(recent.pid, 1234)
            outdated = reader.latest(now_epoch=NOW + 31)
            self.assertEqual(outdated.state, "STALE")
            self.assertIsNone(outdated.protocol_version)
            self.assertEqual(outdated.recorded_epoch, NOW)

    def test_closed_and_unverified_records_never_become_responsive(self):
        self.writer.record(
            observation("UNVERIFIED", pid=123), source_sha256=DIGEST,
            now_epoch=NOW)
        self.writer.record(
            observation("CLOSED", pid=None), source_sha256=DIGEST,
            now_epoch=NOW + 1)
        last = self.writer.latest(now_epoch=NOW + 1)
        self.assertEqual(last.state, "CLOSED")
        self.assertIsNone(last.pid)
        self.assertIsNone(last.protocol_version)

    def test_invalid_state_version_sha_future_and_clock_rollback_rejected(self):
        invalid = [
            (observation("RESPONSIVE", pid=None), DIGEST, NOW),
            (observation("RESPONSIVE", age=32), DIGEST, NOW),
            (observation("RESPONSIVE", age=float("nan")), DIGEST, NOW),
            (McpSessionObservation("RESPONSIVE", 12, 1.0, None), DIGEST, NOW),
            (McpSessionObservation("EXITED", 12, None, PROTOCOL_VERSION), DIGEST, NOW),
            (McpSessionObservation("ACTIVE", 12, 1.0, None), DIGEST, NOW),
            (McpSessionObservation("RESPONSIVE", 12, 0, PROTOCOL_VERSION, True),
             DIGEST, NOW),
            (observation(), "invalid-digest", NOW),
            (observation(), DIGEST, True),
            (observation(), DIGEST, -1),
            (observation(), DIGEST, 4_102_444_801),
        ]
        for obs, digest, when in invalid:
            with self.subTest(state=obs.state, digest=digest, when=when):
                with self.assertRaises(McpObservationError):
                    self.writer.record(obs, source_sha256=digest, now_epoch=when)
        self.assertIsNone(self.writer.latest(now_epoch=NOW))
        self.writer.record(observation(), source_sha256=DIGEST, now_epoch=NOW)
        with self.assertRaisesRegex(McpObservationError, "chronological"):
            self.writer.record(observation(), source_sha256=DIGEST,
                               now_epoch=NOW - 1)

    def test_read_only_reader_cannot_create_or_update_journal(self):
        missing = Path(self.temp.name) / "uncreated.sqlite"
        with self.assertRaises(McpObservationError):
            HubObservationJournal(missing, readonly=True)
        self.assertFalse(missing.exists())
        with HubObservationJournal(self.db, readonly=True) as reader:
            with self.assertRaisesRegex(McpObservationError, "Read-only"):
                reader.record(observation(), source_sha256=DIGEST,
                              now_epoch=NOW)
            with self.assertRaises(sqlite3.OperationalError):
                reader.db.execute("DELETE FROM mcp_hub_observations")

    def test_bounded_ring_keeps_only_last_128_observations(self):
        for n in range(MAX_JOURNAL_EVENTS + 40):
            self.writer.record(
                observation("UNVERIFIED", pid=42),
                source_sha256=DIGEST, now_epoch=NOW + n)
        count = self.writer.db.execute(
            "SELECT count(*) FROM mcp_hub_observations").fetchone()[0]
        self.assertEqual(count, MAX_JOURNAL_EVENTS)
        recent = self.writer.latest(now_epoch=NOW + MAX_JOURNAL_EVENTS + 40)
        self.assertEqual(recent.sequence, MAX_JOURNAL_EVENTS + 40)
        self.assertEqual(recent.state, "UNVERIFIED")

    def test_corrupt_database_proof_is_rejected_without_showing_fake_state(self):
        self.writer.record(observation(), source_sha256=DIGEST, now_epoch=NOW)
        self.writer.db.execute(
            "UPDATE mcp_hub_observations SET source_sha256='forged'")
        with self.assertRaisesRegex(McpObservationError, "Corrupted"):
            self.writer.latest(now_epoch=NOW)

    def test_live_internal_hub_is_recorded_without_proving_plugins(self):
        interpreter = Path(sys.executable).resolve()
        digest = hashlib.sha256(interpreter.read_bytes()).hexdigest()
        pin = PinnedLocalPlugin("python", "3.13", interpreter, digest)
        with LocalMcpHubSession(
                interpreter_pin=pin, package_root=ROOT,
                response_timeout=8) as session:
            self.assertEqual(session.discover().state, "RESPONSIVE")
            seq = self.writer.record_live_session(session, now_epoch=NOW)
            self.assertEqual(seq, 1)
            row = self.writer.latest(now_epoch=NOW)
            self.assertEqual(row.state, "RESPONSIVE")
            self.assertEqual(row.source_sha256, digest)
            self.assertEqual(row.pid, session._process.pid)
        self.writer.record_live_session(session, now_epoch=NOW + 1)
        self.assertEqual(self.writer.latest(now_epoch=NOW + 1).state, "CLOSED")


if __name__ == "__main__":
    unittest.main()

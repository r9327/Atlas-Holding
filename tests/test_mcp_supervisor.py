"""Real internal MCP supervisor lifecycle, pause and resource pressure tests."""
import hashlib
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from holding.mcp_local_inventory import PinnedLocalPlugin
from holding.mcp_hub_journal import HubObservationJournal
from holding.mcp_supervisor import InternalMcpSupervisor, McpSupervisorError

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable).resolve()
NOW = 1_800_000_000


def interpreter_pin():
    hash_ = hashlib.sha256()
    with PYTHON.open("rb") as file:
        for part in iter(lambda: file.read(64 * 1024), b""):
            hash_.update(part)
    return PinnedLocalPlugin("python", "3.13", PYTHON, hash_.hexdigest())


class InternalMcpSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "engine.sqlite"
        # sqlite3.Connection's context manager does NOT close the handle.
        # On native Windows this would leave engine.sqlite locked at teardown.
        with closing(sqlite3.connect(self.db)) as conn:
            with conn:
                conn.execute("CREATE TABLE missions (id TEXT PRIMARY KEY)")
                conn.execute("INSERT INTO missions VALUES ('task-1')")
        self.pin = interpreter_pin()
        if os.name == "nt":
            # GitHub runner's Python installation is not provisioned by
            # AtlasSetup yet. Test the live Job Object/protocol separately
            # from the native ACL gate, which has its own real Windows tests.
            patcher = patch("holding.mcp_supervisor.require_private_windows_path")
            patcher.start()
            self.addCleanup(patcher.stop)

    def supervisor(self):
        return InternalMcpSupervisor(
            database=self.db, interpreter_pin=self.pin,
            package_root=ROOT, response_timeout=8)

    def test_no_hub_is_launched_during_pause_or_resource_pressure(self):
        with self.supervisor() as supervisor:
            self.assertEqual(supervisor._session.observe().state, "NOT_STARTED")
            paused = supervisor.step(
                background_allowed=False, resources_safe=True, now_epoch=NOW)
            self.assertEqual(paused.state, "CLOSED")
            self.assertIsNone(paused.pid)
            self.assertEqual(supervisor._session.observe().state, "CLOSED")
            pressured = supervisor.step(
                background_allowed=True, resources_safe=False, now_epoch=NOW + 1)
            self.assertEqual(pressured.state, "CLOSED")
            self.assertEqual(pressured.recorded_epoch, NOW + 1)
            with HubObservationJournal(self.db, readonly=True) as reader:
                self.assertEqual(reader.latest(now_epoch=NOW + 1).state, "CLOSED")

    def test_real_discovery_restart_pause_stop_and_engine_shared_db(self):
        with self.supervisor() as supervisor:
            first = supervisor.step(
                background_allowed=True, resources_safe=True, now_epoch=NOW)
            self.assertEqual(first.state, "RESPONSIVE")
            self.assertEqual(first.source_sha256, self.pin.expected_sha256)
            self.assertIsNotNone(first.pid)
            self.assertIsNone(supervisor._session._process.poll())
            second = supervisor.step(
                background_allowed=True, resources_safe=True, now_epoch=NOW + 1)
            self.assertEqual(second.state, "RESPONSIVE")
            self.assertEqual(second.pid, first.pid)
            with HubObservationJournal(self.db, readonly=True) as reader:
                self.assertEqual(reader.latest(now_epoch=NOW + 1).pid, first.pid)
            paused = supervisor.step(
                background_allowed=False, resources_safe=True, now_epoch=NOW + 2)
            self.assertEqual(paused.state, "CLOSED")
            self.assertIsNone(supervisor._session._process)
            resumed = supervisor.step(
                background_allowed=True, resources_safe=True, now_epoch=NOW + 3)
            self.assertEqual(resumed.state, "RESPONSIVE")
            self.assertNotEqual(resumed.pid, first.pid)
            with closing(sqlite3.connect(self.db)) as connection:
                self.assertEqual(
                    connection.execute("SELECT id FROM missions").fetchone()[0],
                    "task-1",
                )
            supervisor.close(now_epoch=NOW + 4)
            with HubObservationJournal(self.db, readonly=True) as reader:
                self.assertEqual(reader.latest(now_epoch=NOW + 4).state, "CLOSED")
            with self.assertRaises(McpSupervisorError):
                supervisor.step(background_allowed=True, resources_safe=True,
                                now_epoch=NOW + 5)

    def test_disabled_or_untrusted_bootstraps_fail_closed(self):
        with self.assertRaises(ValueError):
            InternalMcpSupervisor(
                database=Path("engine.sqlite"),
                interpreter_pin=self.pin, package_root=ROOT)
        with self.assertRaises(ValueError):
            InternalMcpSupervisor(
                database=self.db, interpreter_pin=self.pin,
                package_root=ROOT.parent)
        with self.supervisor() as supervisor:
            with self.assertRaises(ValueError):
                supervisor.step(
                    background_allowed=1, resources_safe=True, now_epoch=NOW)
            with self.assertRaises(ValueError):
                supervisor.step(
                    background_allowed=True, resources_safe=True, now_epoch=True)
            self.assertEqual(supervisor._session.observe().state, "NOT_STARTED")

    def test_corrupted_binary_is_never_launched(self):
        with self.supervisor() as supervisor:
            supervisor._session._pin = PinnedLocalPlugin(
                self.pin.name, self.pin.declared_version,
                self.pin.executable, "f" * 64)
            with self.assertRaisesRegex(McpSupervisorError, "launch failed"):
                supervisor.step(
                    background_allowed=True, resources_safe=True, now_epoch=NOW)
            with HubObservationJournal(self.db, readonly=True) as reader:
                self.assertNotEqual(
                    reader.latest(now_epoch=NOW).state, "RESPONSIVE")


if __name__ == "__main__":
    unittest.main()

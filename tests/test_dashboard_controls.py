"""Real SQLite control mutations over separately authenticated loopback frames."""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest

from holding.control import ControlPlane
from holding.dashboard_ipc import (
    DashboardControlClient, DashboardIpcClient, DashboardIpcError,
    DashboardIpcServer,
)
from holding.state_store import StateStore

READ = b"r" * 32
WRITE = b"w" * 32


class DashboardControlTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "holding.sqlite"
        with StateStore(self.db):
            pass
        self.server = DashboardIpcServer.for_database(
            self.db, engine_id="engine-secured",
            secret=READ, control_secret=WRITE,
        )
        self.server.start()
        self.addCleanup(self.server.close)

    def flags(self):
        with ControlPlane(self.db) as control:
            state = control.snapshot()
            return state.paused, state.stop_requested, state.last_event_seq

    def client(self, key=WRITE):
        return DashboardControlClient(port=self.server.port, secret=key)

    def test_pause_resume_stop_persist_and_log_without_code_execution(self):
        self.assertEqual(self.flags()[:2], (False, False))
        first = self.flags()[2]
        self.client().send("pause")
        paused = self.flags()
        self.assertEqual(paused[:2], (True, False))
        self.assertGreater(paused[2], first)
        self.client().send("resume")
        self.assertEqual(self.flags()[:2], (False, False))
        self.client().send("stop")
        self.assertEqual(self.flags()[:2], (False, True))
        with self.assertRaisesRegex(DashboardIpcError, "Control refused"):
            self.client().send("resume")
        self.assertEqual(self.flags()[:2], (False, True))
        with StateStore(self.db) as store:
            logs = [r["event"] for r in store.db.execute(
                "SELECT event FROM events ORDER BY seq")]
        self.assertIn("PAUSED", logs)
        self.assertIn("RESUMED", logs)
        self.assertIn("STOP_REQUESTED", logs)
        self.assertNotIn("STOP_RESET", logs)

    def test_readonly_key_cannot_control_or_upgrade_permissions(self):
        before = self.flags()
        with self.assertRaises(DashboardIpcError):
            self.client(READ).send("stop")
        self.assertEqual(self.flags(), before)
        self.assertEqual(
            DashboardIpcClient(port=self.server.port, secret=READ,
                               engine_id="engine-secured").fetch().engine_id,
            "engine-secured",
        )
        for illegal in ("clear_stop", "merge", "execute", "", 4):
            with self.subTest(illegal=illegal), self.assertRaises(DashboardIpcError):
                self.client().send(illegal)
        self.assertEqual(self.flags(), before)

    def test_no_control_key_means_readonly_server(self):
        server = DashboardIpcServer.for_database(
            self.db, engine_id="engine", secret=READ)
        with server:
            with self.assertRaises(DashboardIpcError):
                DashboardControlClient(port=server.port, secret=WRITE).send("stop")
        self.assertFalse(self.flags()[1])

    def test_same_key_and_partial_authority_are_rejected(self):
        with self.assertRaises(DashboardIpcError):
            DashboardIpcServer.for_database(
                self.db, engine_id="engine",
                secret=READ, control_secret=READ)
        with self.assertRaises(DashboardIpcError):
            DashboardIpcServer(READ, lambda: b"{}", control_secret=WRITE)
        with self.assertRaises(DashboardIpcError):
            DashboardIpcServer(READ, lambda: b"{}", control_handler=lambda op: None)

    def _frame(self, op: bytes, *, when=None, nonce=b"n" * 32, key=WRITE):
        when = int(time.time()) if when is None else when
        stamp = struct.pack("!Q", when)
        digest = hmac.new(
            key, b"control-request" + op + stamp + nonce,
            hashlib.sha256).digest()
        return b"AH02" + op + stamp + nonce + digest

    def _raw(self, frame: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.server.port),
                                      timeout=2) as conn:
            conn.settimeout(2)
            conn.sendall(frame)
            return conn.recv(128)

    def test_authenticated_command_replay_rejected(self):
        frame = self._frame(b"P")
        accepted = self._raw(frame)
        self.assertEqual(accepted[:5], b"AH02A")
        once = self.flags()[2]
        self.assertEqual(self._raw(frame), b"")
        self.assertEqual(self.flags()[2], once)

    def test_tampering_wrong_action_old_timestamp_fail_closed(self):
        before = self.flags()
        cases = [
            self._frame(b"S", key=READ),
            self._frame(b"S", when=int(time.time()) - 100),
            self._frame(b"M"),
            self._frame(b"P")[:-1] + b"x",
        ]
        for frame in cases:
            with self.subTest(frame=frame[:5]):
                self.assertEqual(self._raw(frame), b"")
        self.assertEqual(self.flags(), before)

    def test_read_frames_still_work_when_write_enabled(self):
        state = DashboardIpcClient(port=self.server.port, secret=READ,
                                   engine_id="engine-secured").fetch()
        self.assertEqual(state.engine_id, "engine-secured")
        self.assertEqual(self.flags()[:2], (False, False))


if __name__ == "__main__":
    unittest.main()

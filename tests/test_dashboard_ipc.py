"""End-to-end local loopback tests with real durable SQLite projections."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.autonomy import Mission
from holding.control import ControlPlane
from holding.control_center_projection import project_dashboard_snapshot
from holding.dashboard_ipc import (
    DashboardIpcClient, DashboardIpcError, DashboardIpcServer,
)
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore


KEY = b"q" * 32
ENGINE = "engine-verified"


class DashboardIpcTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "holding.sqlite"
        with StateStore(self.path) as store:
            store.add_missions((Mission("first", "r9327/Dof-Atlas"),))
        self.server = DashboardIpcServer.for_database(
            self.path, engine_id=ENGINE, secret=KEY)
        self.server.start()
        self.addCleanup(self.server.close)

    def client(self, **updates):
        params = {"port": self.server.port, "secret": KEY, "engine_id": ENGINE}
        params.update(updates)
        return DashboardIpcClient(**params)

    def test_reads_real_sqlite_without_exposing_storage_to_gui(self):
        client = self.client()
        first = client.fetch()
        self.assertEqual(first.engine_id, ENGINE)
        self.assertEqual(first.tasks[0].task_id, "first")
        self.assertEqual(first.tasks[0].state, "READY")
        self.assertIsNone(first.tasks[0].candidate_sha)
        # The client possesses no ControlPlane, SQLite connection, or setter.
        self.assertFalse(hasattr(client, "store"))
        self.assertFalse(hasattr(client, "pause"))
        with StateStore(self.path) as store:
            store._event("first", "MISSION_BLOCKED", "token=never-to-ui")
        updated = client.fetch()
        self.assertGreater(updated.sequence, first.sequence)
        self.assertEqual(updated.logs[-1].message, "MISSION_BLOCKED")
        self.assertNotIn("never-to-ui", repr(updated))
        self.assertEqual(client.fetch().sequence, updated.sequence)

    def test_wrong_key_or_wrong_engine_fails_closed(self):
        with self.assertRaises(DashboardIpcError):
            self.client(secret=b"z" * 32).fetch()
        with self.assertRaises(DashboardIpcError):
            self.client(engine_id="other-engine").fetch()
        self.assertEqual(self.client().fetch().engine_id, ENGINE)

    def test_arbitrary_command_cannot_be_interpreted_as_engine_control(self):
        with socket.create_connection(("127.0.0.1", self.server.port),
                                      timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(b"STOP".ljust(68, b"x"))
            self.assertEqual(sock.recv(1), b"")
        with ControlPlane(self.path) as controller:
            status = controller.snapshot()
            self.assertFalse(status.stop_requested)
            self.assertFalse(status.paused)
        self.assertEqual(self.client().fetch().tasks[0].state, "READY")

    def test_authenticated_frame_replay_is_rejected(self):
        nonce = b"n" * 32
        message = b"AH01" + nonce + hmac.new(
            KEY, b"request" + nonce, hashlib.sha256).digest()
        with socket.create_connection(("127.0.0.1", self.server.port),
                                      timeout=2) as sock:
            sock.sendall(message)
            self.assertEqual(sock.recv(4), b"AH01")
        with socket.create_connection(("127.0.0.1", self.server.port),
                                      timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(message)
            self.assertEqual(sock.recv(4), b"")
        self.assertEqual(self.client().fetch().engine_id, ENGINE)

    def test_service_owns_ipc_lifecycle_independent_of_window(self):
        class IdleWorker:
            def ready(self):
                return True

            def __call__(self, _mission):
                raise AssertionError("Dashboard did not request code execution")

        service = HoldingService(
            ServiceSettings(self.path, idle_seconds=0.05),
            IdleWorker(), online=lambda: False)
        try:
            with self.assertRaises(RuntimeError):
                service.enable_dashboard_ipc(secret=KEY, engine_id=ENGINE)
            service.start()
            port = service.enable_dashboard_ipc(
                secret=b"r" * 32, engine_id="service-engine")
            with self.assertRaises(RuntimeError):
                service.enable_dashboard_ipc(
                    secret=b"r" * 32, engine_id="service-engine")
            client = DashboardIpcClient(
                port=port, secret=b"r" * 32, engine_id="service-engine")
            self.assertEqual(client.fetch().tasks[0].state, "READY")
            self.assertNotIn(
                service.run(max_ticks=1).action, ("STOPPED", "LEASE_LOST"))
            self.assertEqual(client.fetch().engine_id, "service-engine")
        finally:
            service.close()
        with self.assertRaises(DashboardIpcError):
            client.fetch()

    def test_invalid_key_port_and_missing_database_never_bind(self):
        for invalid in (b"short", "not-bytes", b"x" * 129):
            with self.subTest(invalid=invalid), self.assertRaises(DashboardIpcError):
                DashboardIpcServer(invalid, lambda: b"{}")
        with self.assertRaises(DashboardIpcError):
            DashboardIpcClient(port=0, secret=KEY, engine_id=ENGINE)
        with self.assertRaises(DashboardIpcError):
            DashboardIpcServer.for_database(
                self.path.parent / "missing.sqlite",
                engine_id=ENGINE, secret=KEY)

    def test_invalid_or_forged_quality_snapshot_rejected_after_mac(self):
        with ControlPlane(self.path) as controller:
            raw = project_dashboard_snapshot(
                controller, engine_id=ENGINE, now_epoch=int(time.time()))
        payload = json.loads(raw)
        payload["tasks"][0].update({
            "state": "DONE", "progress": "COMPLETED",
            "phase": "CERTIFICATION", "candidate_sha": "a" * 40,
            "completion_evidence": None,
        })
        forged = json.dumps(payload).encode()
        server = DashboardIpcServer(KEY, lambda: forged)
        with server:
            with self.assertRaisesRegex(DashboardIpcError, "Invalid authenticated"):
                DashboardIpcClient(
                    port=server.port, secret=KEY, engine_id=ENGINE).fetch()
        self.assertEqual(self.client().fetch().tasks[0].state, "READY")

    def test_bad_supplier_and_oversized_result_do_not_send_fake_data(self):
        for response in (b"", b"x" * (2 * 1024 * 1024 + 1)):
            with self.subTest(length=len(response)):
                server = DashboardIpcServer(KEY, lambda: response)
                with server:
                    with self.assertRaises(DashboardIpcError):
                        DashboardIpcClient(
                            port=server.port, secret=KEY, engine_id=ENGINE).fetch()

    def test_service_disappears_cleanly_on_close(self):
        server = DashboardIpcServer(KEY, lambda: b"{}")
        server.start()
        port = server.port
        server.close()
        with self.assertRaises(DashboardIpcError):
            DashboardIpcClient(port=port, secret=KEY, engine_id=ENGINE).fetch()


if __name__ == "__main__":
    unittest.main()

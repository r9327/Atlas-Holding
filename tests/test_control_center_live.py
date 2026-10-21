"""Qt offscreen contract for live authenticated read-only display."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtWidgets import QApplication, QProgressBar

from holding.autonomy import Mission
from holding.control_center_live import LiveControlCenterWindow
from holding.dashboard_ipc import DashboardIpcClient, DashboardIpcServer
from holding.state_store import StateStore


KEY = b"a" * 32
ENGINE = "live-engine-1"


class LiveDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "service.sqlite"
        with StateStore(self.db) as store:
            store.add_missions([Mission("coding", "r9327/Dof-Atlas")])
        self.server = DashboardIpcServer.for_database(
            self.db, engine_id=ENGINE, secret=KEY)
        self.server.start()
        self.addCleanup(self.server.close)
        self.client = DashboardIpcClient(
            port=self.server.port, secret=KEY, engine_id=ENGINE)

    def until(self, condition, seconds=4):
        expiry = time.monotonic() + seconds
        while time.monotonic() < expiry:
            self.app.processEvents()
            if condition():
                return
            time.sleep(.02)
        self.fail("Qt did not display the authenticated state")

    def test_ui_consumes_real_engine_events_without_qt_blocking(self):
        window = LiveControlCenterWindow(self.client, refresh_ms=250)
        try:
            self.until(lambda: window.tasks.rowCount() == 1)
            self.assertEqual(window.tasks.item(0, 1).text(), "READY")
            self.assertEqual(window.findChildren(QProgressBar), [])
            old = window.displayed_sequence
            with StateStore(self.db) as store:
                store._event("coding", "TEST_EVENT", "access-token=secret")
            self.until(lambda: window.displayed_sequence is not None
                       and window.displayed_sequence > old)
            self.assertEqual(window.logs.item(window.logs.rowCount() - 1, 2).text(),
                             "TEST_EVENT")
            self.assertNotIn("access-token", window.status.text())
        finally:
            window.close()

    def test_lost_connection_is_not_presented_as_live(self):
        window = LiveControlCenterWindow(self.client, refresh_ms=250)
        try:
            self.until(lambda: window.tasks.rowCount() == 1)
            self.server.close()
            self.until(lambda: "Déconnecté" in window.status.text(),
                       seconds=6)
            self.assertEqual(window.tasks.item(0, 1).text(), "READY")
            self.assertIn("non actualisé", window.status.text())
        finally:
            window.close()

    def test_closing_window_does_not_interrupt_running_headless_host(self):
        window = LiveControlCenterWindow(self.client, refresh_ms=250)
        self.until(lambda: window.tasks.rowCount() == 1)
        self.assertTrue(window.close())
        self.assertEqual(self.client.fetch().tasks[0].state, "READY")

    def test_input_constraints_refuse_invalid_refresh_or_client(self):
        with self.assertRaises(ValueError):
            LiveControlCenterWindow("unauthenticated")
        with self.assertRaises(ValueError):
            LiveControlCenterWindow(self.client, refresh_ms=0)


if __name__ == "__main__":
    unittest.main()

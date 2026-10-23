"""Qt offscreen contract for live authenticated read-only display."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from threading import Event
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox, QProgressBar

from holding.autonomy import Mission
from holding.control_center_live import LiveControlCenterWindow
from holding.control import ControlPlane
from holding.dashboard_ipc import (
    DashboardControlClient, DashboardIpcClient, DashboardIpcServer,
)
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

    def test_authorized_buttons_pause_resume_stop_and_log(self):
        write_key = b"z" * 32
        trusted_host = DashboardIpcServer.for_database(
            self.db, engine_id=ENGINE, secret=KEY,
            control_secret=write_key)
        with trusted_host:
            read = DashboardIpcClient(
                port=trusted_host.port, secret=KEY, engine_id=ENGINE)
            write = DashboardControlClient(
                port=trusted_host.port, secret=write_key)
            window = LiveControlCenterWindow(
                read, control=write, refresh_ms=250)
            def flags():
                with ControlPlane(self.db) as controller:
                    state = controller.snapshot()
                    return state.paused, state.stop_requested
            try:
                self.until(lambda: window.pause_button.isEnabled())
                window.pause_button.click()
                self.until(lambda: flags()[0])
                self.until(lambda: window.resume_button.isEnabled())
                window.resume_button.click()
                self.until(lambda: flags() == (False, False))
                self.until(lambda: window.stop_button.isEnabled())
                with patch.object(QMessageBox, "question",
                                  return_value=QMessageBox.StandardButton.No):
                    window.stop_button.click()
                self.assertEqual(flags(), (False, False))
                with patch.object(QMessageBox, "question",
                                  return_value=QMessageBox.StandardButton.Yes):
                    window.stop_button.click()
                self.until(lambda: flags()[1])
                self.until(lambda: not window.stop_button.isEnabled()
                           and "ARRÊT DEMANDÉ" in window.status.text())
                self.assertFalse(window.resume_button.isEnabled())
                self.assertTrue(window.logs.rowCount() > 0)
            finally:
                window.close()

    def test_second_command_waits_for_new_authenticated_durable_state(self):
        write_key = b"v" * 32
        with DashboardIpcServer.for_database(
                self.db, engine_id=ENGINE, secret=KEY,
                control_secret=write_key) as host:
            read = DashboardIpcClient(
                port=host.port, secret=KEY, engine_id=ENGINE)
            write = DashboardControlClient(port=host.port, secret=write_key)
            allow_write = Event()
            original_send = write.send

            def delayed_send(action):
                if not allow_write.wait(3):
                    raise RuntimeError("Timed out waiting for test writer")
                return original_send(action)

            window = LiveControlCenterWindow(
                read, control=write, refresh_ms=250)
            try:
                self.until(lambda: window.pause_button.isEnabled())
                baseline = window.displayed_sequence
                with patch.object(write, "send", side_effect=delayed_send) as send:
                    window.pause_button.click()
                    self.assertIsNotNone(window._awaiting_confirmation)
                    self.assertFalse(window.pause_button.isEnabled())
                    self.assertFalse(window.resume_button.isEnabled())
                    self.assertFalse(window.stop_button.isEnabled())
                    # Programmatic button slots must not bypass the interlock.
                    window.request_pause()
                    window.request_stop()
                    self.assertEqual(send.call_count, 1)
                    allow_write.set()
                    self.until(lambda: window.resume_button.isEnabled())
                self.assertIsNone(window._awaiting_confirmation)
                self.assertGreater(window.displayed_sequence, baseline)
                with StateStore(self.db) as store:
                    count = store.db.execute(
                        "SELECT count(*) FROM events WHERE event='PAUSED'"
                    ).fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                allow_write.set()
                window.close()

    def test_readonly_view_disables_mutating_buttons(self):
        window = LiveControlCenterWindow(self.client, refresh_ms=250)
        try:
            self.until(lambda: window.tasks.rowCount() == 1)
            self.assertFalse(window.pause_button.isEnabled())
            self.assertFalse(window.resume_button.isEnabled())
            self.assertFalse(window.stop_button.isEnabled())
            window.request_pause()
            with ControlPlane(self.db) as controller:
                self.assertFalse(controller.snapshot().paused)
        finally:
            window.close()

    def test_input_constraints_refuse_invalid_refresh_or_client(self):
        with self.assertRaises(ValueError):
            LiveControlCenterWindow("unauthenticated")
        with self.assertRaises(ValueError):
            LiveControlCenterWindow(self.client, refresh_ms=0)


if __name__ == "__main__":
    unittest.main()

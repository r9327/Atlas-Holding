import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
import json
import unittest
import weakref
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QAbstractItemView, QProgressBar

from holding.control_center_state import consume_dashboard_snapshot
from holding.control_center_ui import ControlCenterUiError, ControlCenterWindow
from test_control_center_state import NOW, snapshot


class ControlCenterUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def state(self, *, sequence=42):
        payload = snapshot()
        payload["sequence"] = sequence
        payload["log_page"]["items"][0]["sequence"] = sequence - 1
        return consume_dashboard_snapshot(
            json.dumps(payload).encode("utf-8"),
            trusted_engine_id="engine-1",
            expected_sequence=sequence,
            now_epoch=NOW,
        )

    def test_validated_state_renders_without_fabricated_percentage(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        self.assertTrue(window.render_state(self.state()))
        self.assertEqual(window.tasks.rowCount(), 1)
        self.assertEqual(window.tasks.item(0, 0).text(), "Verify candidate")
        self.assertEqual(window.tasks.item(0, 3).text(), "COMPLETED")
        self.assertEqual(window.tasks.item(0, 5).text(), "CI FULL")
        self.assertIn("CI_PENDING", window.status.text())
        self.assertEqual(window.findChildren(QProgressBar), [])
        self.assertEqual(
            window.tasks.editTriggers(),
            QAbstractItemView.EditTrigger.NoEditTriggers,
        )
        window.close()

    def test_replayed_or_older_state_cannot_replace_display(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        self.assertTrue(window.render_state(self.state(sequence=43)))
        self.assertFalse(window.render_state(self.state(sequence=43)))
        self.assertFalse(window.render_state(self.state(sequence=42)))
        self.assertEqual(window.displayed_sequence, 43)
        window.close()

    def test_wrong_engine_or_unvalidated_object_is_refused(self):
        window = ControlCenterWindow(trusted_engine_id="other-engine")
        with self.assertRaisesRegex(ControlCenterUiError, "another engine"):
            window.render_state(self.state())
        with self.assertRaisesRegex(ControlCenterUiError, "DashboardState"):
            window.render_state({})
        window.close()

    def test_close_does_not_expose_or_trigger_engine_control(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        window.render_state(self.state())
        self.assertFalse(hasattr(window, "engine"))
        self.assertTrue(window.close())

    def test_refresh_reuses_unmodified_cells_and_preserves_selection(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        window.render_state(self.state(sequence=42))
        window.tasks.selectRow(0)
        original = window.tasks.item(0, 0)
        window.render_state(self.state(sequence=43))
        self.assertIs(window.tasks.item(0, 0), original)
        self.assertEqual(window.tasks.currentRow(), 0)

        payload = snapshot()
        payload["sequence"] = 44
        payload["log_page"]["items"][0]["sequence"] = 43
        payload["tasks"][0]["title"] = "Updated from engine"
        refreshed = consume_dashboard_snapshot(
            json.dumps(payload).encode("utf-8"),
            trusted_engine_id="engine-1", expected_sequence=44, now_epoch=NOW,
        )
        self.assertTrue(window.render_state(refreshed))
        self.assertIs(window.tasks.item(0, 0), original)
        self.assertEqual(original.text(), "Updated from engine")
        self.assertEqual(original.toolTip(), "Updated from engine")
        self.assertEqual(window.tasks.currentRow(), 0)
        window.close()

    def test_journal_filter_connections_do_not_keep_closed_window_alive(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        window.render_state(self.state())
        window.log_search.setText("exact")
        reference = weakref.ref(window)
        window.close()
        del window
        gc.collect()
        self.assertIsNone(reference(), "Qt filter signals retained a closed window")

    def test_journal_filters_are_local_and_persist_across_snapshots(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        window.render_state(self.state(sequence=42))
        self.assertEqual(window.logs.rowCount(), 1)
        self.assertFalse(window.logs.isRowHidden(0))
        window.log_category.setCurrentText("QA")
        self.assertTrue(window.logs.isRowHidden(0))
        window.log_category.setCurrentText("CI")
        self.assertFalse(window.logs.isRowHidden(0))
        window.log_search.setText("unknown")
        self.assertTrue(window.logs.isRowHidden(0))
        window.log_search.setText("exact")
        self.assertFalse(window.logs.isRowHidden(0))
        # Refresh does not clear the operator's filters or make network calls.
        self.assertTrue(window.render_state(self.state(sequence=43)))
        self.assertEqual(window.log_category.currentText(), "CI")
        self.assertEqual(window.log_search.text(), "exact")
        self.assertFalse(window.logs.isRowHidden(0))
        window.log_category.setCurrentText("TASK")
        self.assertTrue(window.logs.isRowHidden(0))
        window.close()

    def test_pre_candidate_task_displays_no_fake_sha_or_evidence(self):
        payload = snapshot()
        payload["tasks"][0].update({
            "state": "READY", "phase": "CODING", "progress": "WAITING",
            "candidate_sha": None, "completion_evidence": None,
        })
        state = consume_dashboard_snapshot(
            json.dumps(payload).encode("utf-8"),
            trusted_engine_id="engine-1", expected_sequence=42, now_epoch=NOW,
        )
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        window.render_state(state)
        self.assertEqual(window.tasks.item(0, 4).text(), "—")
        self.assertEqual(window.tasks.item(0, 5).text(), "Aucune")
        window.close()


    def test_protection_view_shows_only_exact_sha_completed_evidence(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        window.render_state(self.state())
        self.assertEqual(window.proofs.rowCount(), 1)
        self.assertEqual(window.proofs.item(0, 1).text(), "CI FULL")
        self.assertEqual(window.proofs.item(0, 2).text(), "a" * 40)
        self.assertEqual(window.proofs.item(0, 3).text(), "b" * 64)
        self.assertIn("Tâches avec preuve CI FULL : 1 / 1",
                      window.protection_status.text())

        payload = snapshot()
        payload["sequence"] = 43
        payload["log_page"]["items"][0]["sequence"] = 42
        payload["tasks"][0].update({
            "state": "QA_PENDING", "phase": "TESTS", "progress": "WAITING",
            "completion_evidence": None,
        })
        unverified = consume_dashboard_snapshot(
            json.dumps(payload).encode("utf-8"),
            trusted_engine_id="engine-1", expected_sequence=43, now_epoch=NOW,
        )
        window.render_state(unverified)
        self.assertEqual(window.proofs.rowCount(), 0)
        self.assertIn("Tâches avec preuve CI FULL : 0 / 1",
                      window.protection_status.text())
        self.assertIn("En attente QA : 1", window.protection_status.text())
        window.close()

    def test_reopening_viewer_rechecks_expired_state(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        with patch("holding.control_center_ui.monotonic", return_value=100):
            window.render_state(self.state())
            window.show()
            self.assertIn("CI_PENDING", window.status.text())
        window.close()
        self.assertFalse(window._status_timer.isActive())

        with patch("holding.control_center_ui.monotonic", return_value=150):
            window.show()
        self.assertIn("ÉTAT PÉRIMÉ", window.status.text())
        self.assertTrue(window._status_timer.isActive())
        window.close()

    def test_silent_connection_expires_and_recovers_only_on_new_snapshot(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        with patch("holding.control_center_ui.monotonic", return_value=100):
            window.render_state(self.state())
        self.assertIn("CI_PENDING", window.status.text())
        self.assertTrue(window._status_timer.isActive())

        with patch("holding.control_center_ui.monotonic", return_value=131):
            window._refresh_connection_status()
        self.assertIn("ÉTAT PÉRIMÉ", window.status.text())
        self.assertNotIn("CI_PENDING", window.status.text())

        # Replay cannot revive an expired engine status.
        with patch("holding.control_center_ui.monotonic", return_value=132):
            self.assertFalse(window.render_state(self.state()))
            window._refresh_connection_status()
        self.assertIn("ÉTAT PÉRIMÉ", window.status.text())

        with patch("holding.control_center_ui.monotonic", return_value=133):
            self.assertTrue(window.render_state(self.state(sequence=43)))
        self.assertIn("CI_PENDING", window.status.text())
        self.assertNotIn("ÉTAT PÉRIMÉ", window.status.text())
        window.close()
        self.assertFalse(window._status_timer.isActive())


if __name__ == "__main__":
    unittest.main()

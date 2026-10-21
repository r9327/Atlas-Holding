import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import unittest

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


if __name__ == "__main__":
    unittest.main()

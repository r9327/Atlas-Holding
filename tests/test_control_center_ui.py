import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
from copy import deepcopy
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


    def test_empty_agent_and_plugin_projections_do_not_claim_offline(self):
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            self.assertTrue(window.render_state(self.state()))
            self.assertIn("1 état(s)", window.agent_telemetry.text())
            self.assertIn("1 état(s)", window.plugin_telemetry.text())

            payload = snapshot()
            payload["sequence"] = 43
            payload["log_page"]["items"][0]["sequence"] = 42
            payload["log_page"]["items"][0]["plugin_name"] = None
            payload["agents"] = []
            payload["plugins"] = []
            empty = consume_dashboard_snapshot(
                json.dumps(payload).encode("utf-8"),
                trusted_engine_id="engine-1",
                expected_sequence=43, now_epoch=NOW,
            )
            self.assertTrue(window.render_state(empty))
            self.assertEqual(window.agents.rowCount(), 0)
            self.assertEqual(window.plugins.rowCount(), 0)
            self.assertIn("disponibilité non déterminable",
                          window.agent_telemetry.text())
            self.assertIn("disponibilité non déterminable",
                          window.plugin_telemetry.text())
            self.assertNotIn("OFFLINE", window.plugin_telemetry.text())
        finally:
            window.close()

    def test_model_history_tab_does_not_fabricate_active_agents(self):
        payload = snapshot()
        payload.update(
            schema_version=4, paused=False, stop_requested=False,
            services=[], model_history=[{
                "role": "CODER", "task_id": "task-1",
                "model_name": "qwen2.5-coder:7b",
                "model_digest": "c" * 64,
                "benchmark_digest": "d" * 64,
                "observed_at": NOW - 2,
            }],
        )
        state = consume_dashboard_snapshot(
            json.dumps(payload).encode("utf-8"),
            trusted_engine_id="engine-1", expected_sequence=42, now_epoch=NOW,
        )
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            self.assertTrue(window.render_state(state))
            self.assertEqual(window.model_history.rowCount(), 1)
            self.assertEqual(window.model_history.item(0, 0).text(), "CODER")
            self.assertEqual(window.model_history.item(0, 1).text(), "task-1")
            self.assertEqual(
                window.model_history.item(0, 2).text(), "qwen2.5-coder:7b",
            )
            self.assertEqual(window.model_history.item(0, 4).text(), "c" * 64)
            self.assertIn("ne signifie ni", window.model_history_note.text())
            self.assertEqual(window.agents.rowCount(), 1)
            self.assertEqual(window.agents.item(0, 2).text(), "IDLE")
        finally:
            window.close()

    def test_model_history_filters_are_local_and_survive_new_snapshot(self):
        payload = snapshot()
        payload.update(
            schema_version=4, paused=False, stop_requested=False,
            services=[], model_history=[
                {
                    "role": "CODER", "task_id": "task-1",
                    "model_name": "qwen-coder",
                    "model_digest": "c" * 64,
                    "benchmark_digest": "d" * 64,
                    "observed_at": NOW - 2,
                },
                {
                    "role": "AUDITOR", "task_id": "task-1",
                    "model_name": "qwen-auditor",
                    "model_digest": "e" * 64,
                    "benchmark_digest": "f" * 64,
                    "observed_at": NOW - 3,
                },
            ],
        )
        def parse():
            return consume_dashboard_snapshot(
                json.dumps(payload).encode("utf-8"),
                trusted_engine_id="engine-1",
                expected_sequence=payload["sequence"], now_epoch=NOW,
            )
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            window.render_state(parse())
            self.assertEqual(window.model_history.rowCount(), 2)
            window.model_role.setCurrentText("CODER")
            self.assertFalse(window.model_history.isRowHidden(0))
            self.assertTrue(window.model_history.isRowHidden(1))
            window.model_search.setText("not-the-model")
            self.assertTrue(window.model_history.isRowHidden(0))
            window.model_search.setText("qwen-coder")
            self.assertFalse(window.model_history.isRowHidden(0))
            payload["sequence"] = 43
            payload["log_page"]["items"][0]["sequence"] = 42
            window.render_state(parse())
            self.assertEqual(window.model_role.currentText(), "CODER")
            self.assertEqual(window.model_search.text(), "qwen-coder")
            self.assertFalse(window.model_history.isRowHidden(0))
            self.assertTrue(window.model_history.isRowHidden(1))
        finally:
            window.close()

    def test_service_health_tab_does_not_claim_plugin_installation(self):
        payload = snapshot()
        payload.update(
            schema_version=3, paused=False, stop_requested=False,
            services=[
                {"name": "github", "status": "ONLINE", "age_seconds": 12},
                {"name": "firecrawl", "status": "STALE", "age_seconds": 901},
            ],
        )
        state = consume_dashboard_snapshot(
            json.dumps(payload).encode("utf-8"),
            trusted_engine_id="engine-1", expected_sequence=42, now_epoch=NOW,
        )
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            self.assertTrue(window.render_state(state))
            self.assertEqual(window.services.rowCount(), 2)
            self.assertEqual(window.services.item(0, 0).text(), "github")
            self.assertEqual(window.services.item(0, 1).text(), "ONLINE")
            self.assertEqual(window.services.item(1, 1).text(), "STALE")
            self.assertIn("pas une preuve d'installation",
                          window.services_note.text())
            self.assertEqual(window.plugins.rowCount(), 1)
            self.assertNotIn("firecrawl", window.plugin_telemetry.text())
        finally:
            window.close()

    def test_bounded_large_roadmap_and_log_page_keep_unmodified_qt_cells(self):
        payload = snapshot()
        template = deepcopy(payload["tasks"][0])
        template.update({
            "state": "READY", "phase": "CODING", "progress": "WAITING",
            "candidate_sha": None, "completion_evidence": None,
        })
        payload["tasks"] = []
        for index in range(350):
            row = deepcopy(template)
            row["id"] = f"task-{index}"
            row["title"] = f"Roadmap operation {index}"
            payload["tasks"].append(row)
        payload["sequence"] = 500
        payload["log_page"]["items"] = []
        for index in range(200):
            payload["log_page"]["items"].append({
                "sequence": 300 + index, "category": "CI",
                "message": f"CI log event {index}",
                "task_id": f"task-{index}", "plugin_name": "github",
                "evidence_digest": None,
            })
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        def validate():
            return consume_dashboard_snapshot(
                json.dumps(payload).encode("utf-8"),
                trusted_engine_id="engine-1",
                expected_sequence=payload["sequence"], now_epoch=NOW,
            )
        try:
            self.assertTrue(window.render_state(validate()))
            self.assertEqual(window.tasks.rowCount(), 350)
            self.assertEqual(window.logs.rowCount(), 200)
            self.assertEqual(window.proofs.rowCount(), 0)
            window.tasks.selectRow(111)
            kept = window.tasks.item(111, 0)
            payload["sequence"] = 501
            payload["tasks"][222]["title"] = "New title from authenticated host"
            self.assertTrue(window.render_state(validate()))
            self.assertIs(window.tasks.item(111, 0), kept)
            self.assertEqual(window.tasks.currentRow(), 111)
            self.assertEqual(
                window.tasks.item(222, 0).text(),
                "New title from authenticated host",
            )
            window.log_search.setText("log event 199")
            self.assertTrue(window.logs.isRowHidden(0))
            self.assertFalse(window.logs.isRowHidden(199))
        finally:
            window.close()

    def test_project_git_state_is_display_only_and_repository_scoped(self):
        from types import SimpleNamespace
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        git_status = SimpleNamespace(
            repository="r9327/Dof-Atlas",
            head="a" * 40, remote_head="a" * 40,
            ahead=0, behind=0, remote_checked=True, dirty=False,
        )
        try:
            window.render_project_status(
                project="DOFUS Atlas", repository="r9327/Dof-Atlas",
                git_status=git_status, eligible_missions=2,
            )
            self.assertIn("Git : à jour", window.project_status.text())
            before = window.project_status.text()
            with self.assertRaisesRegex(ValueError, "another repository"):
                window.render_project_status(
                    project="Other", repository="r9327/Atlas-Holding",
                    git_status=git_status, eligible_missions=2,
                )
            self.assertEqual(window.project_status.text(), before)
            self.assertEqual(window.displayed_sequence, None)
        finally:
            window.close()

    def test_v2_sync_evidence_is_not_misreported_as_ci_certification(self):
        payload = snapshot()
        task = payload["tasks"][0]
        task["title"] = "push-one · push"
        task["completion_evidence"].update({
            "kind": "SYNC", "scope": "SCOPED",
            "outcome": "SUCCESS", "source": "github_sync_attempts",
        })
        payload.update(schema_version=2, paused=False, stop_requested=False)
        state = consume_dashboard_snapshot(
            json.dumps(payload).encode("utf-8"),
            trusted_engine_id="engine-1", expected_sequence=42, now_epoch=NOW,
        )
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            self.assertTrue(window.render_state(state))
            self.assertEqual(window.proofs.rowCount(), 1)
            self.assertEqual(window.proofs.item(0, 1).text(), "SYNC SCOPED")
            self.assertIn("Tâches avec preuve CI FULL : 0 / 1",
                          window.protection_status.text())
            self.assertIn("Synchronisations prouvées : 1",
                          window.protection_status.text())
            self.assertEqual(window.tasks.item(0, 5).text(), "SYNC SCOPED")
        finally:
            window.close()

    def test_heartbeat_requires_same_engine_and_exact_displayed_sequence(self):
        state = self.state()
        newer = self.state(sequence=43)
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            self.assertFalse(window.acknowledge_authenticated_state(state))
            with patch("holding.control_center_ui.monotonic", return_value=100):
                window.render_state(state)
            with patch("holding.control_center_ui.monotonic", return_value=140):
                window._refresh_connection_status()
            self.assertIn("ÉTAT PÉRIMÉ", window.status.text())
            self.assertFalse(window.acknowledge_authenticated_state(newer))
            self.assertIn("ÉTAT PÉRIMÉ", window.status.text())
            with self.assertRaisesRegex(ControlCenterUiError, "another engine"):
                other = self.state()
                from dataclasses import replace
                window.acknowledge_authenticated_state(
                    replace(other, engine_id="foreign-engine"))
            with patch("holding.control_center_ui.monotonic", return_value=141):
                self.assertTrue(window.acknowledge_authenticated_state(state))
            self.assertIn("CI_PENDING", window.status.text())
            self.assertNotIn("ÉTAT PÉRIMÉ", window.status.text())
            self.assertEqual(window.displayed_sequence, 42)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()

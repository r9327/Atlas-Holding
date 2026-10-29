"""Autonomie V2 F02: real SQLite -> authenticated v7 snapshot -> Qt."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.control import ControlPlane
from holding.control_center_projection import project_dashboard_snapshot
from holding.control_center_state import (
    ControlCenterStateError, consume_dashboard_snapshot,
)
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
NOW = 1_800_000_000


class MissionPreflightDashboardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "state.sqlite"
        with StateStore(self.path) as store:
            store.add_missions([Mission("primary", REPO)])

    def grant(self, operation="code"):
        with StateStore(self.path) as store:
            store.grant_for_onboarding(
                StandingGrant(REPO, frozenset({operation}), NOW + 1000))

    def read(self):
        with ControlPlane(self.path) as control:
            before = control.store.db.total_changes
            raw = project_dashboard_snapshot(
                control, engine_id="eng-f02", now_epoch=NOW)
            self.assertEqual(before, control.store.db.total_changes)
        payload = json.loads(raw)
        state = consume_dashboard_snapshot(
            raw, trusted_engine_id="eng-f02",
            expected_sequence=payload["sequence"], now_epoch=NOW)
        return payload, state

    def test_missing_grant_is_visible_and_not_a_permission(self):
        payload, state = self.read()
        self.assertEqual(payload["schema_version"], 7)
        self.assertEqual(payload["preflight_total_ready"], 1)
        self.assertEqual(len(state.mission_preflights), 1)
        item = state.mission_preflights[0]
        self.assertEqual(item.mission_id, "primary")
        self.assertEqual(item.status, "BLOCKED")
        self.assertEqual(item.permission, "MISSING_OR_EXPIRED")
        self.assertIn("NO_ACTIVE_SCOPED_GRANT", item.blockers)
        self.assertFalse(payload["mission_preflights"][0]["executes_actions"])
        self.assertEqual(payload["mission_preflights"][0]["model_benchmark"], "NOT_RUN")
        self.assertEqual(payload["mission_preflights"][0]["worker_validation"], "NOT_RUN")

    def test_existing_grant_never_promotes_a_task_to_running(self):
        self.grant()
        payload, state = self.read()
        self.assertEqual(state.mission_preflights[0].status, "PRECHECK_PASSED")
        self.assertEqual(state.tasks[0].state, "READY")
        self.assertEqual(payload["mission_preflights"][0]["network_verification"], "NOT_RUN")
        with ControlPlane(self.path) as control:
            from holding.mission_preflight import inspect_mission_in_connection
            self.assertEqual(inspect_mission_in_connection(
                control.store.db, "primary", now_epoch=NOW).status, "PRECHECK_PASSED")
            self.assertEqual(control.store.missions()[0].state, "READY")

    def test_independent_task_preflight_continues_while_other_is_deferred(self):
        with StateStore(self.path) as store:
            store.add_missions([
                Mission("dependent", REPO, dependencies=("primary",), priority=8),
                Mission("independent", REPO, priority=4),
            ])
        self.grant()
        _, state = self.read()
        by_id = {p.mission_id: p for p in state.mission_preflights}
        self.assertEqual(by_id["dependent"].status, "WAITING_FOR_DEPENDENCIES")
        self.assertEqual(by_id["dependent"].waiting_for, ("primary",))
        self.assertEqual(by_id["independent"].status, "PRECHECK_PASSED")
        with StateStore(self.path) as store:
            store.db.execute(
                "UPDATE missions SET state='DONE',candidate_sha=?,evidence_sha=? "
                "WHERE id='primary'", ("a" * 40, "b" * 40))
        _, state = self.read()
        self.assertEqual(next(x for x in state.mission_preflights
                              if x.mission_id == "dependent").waiting_for, ("primary",))

    def test_pause_and_external_health_never_become_automatic_success(self):
        with StateStore(self.path) as store:
            store.add_missions([
                Mission("external", REPO, operation="push", external_required=True),
            ])
            store.db.execute("UPDATE settings SET value='1' WHERE key='paused'")
            store.grant_for_onboarding(StandingGrant(
                REPO, frozenset({"push"}), NOW + 1000))
        _, state = self.read()
        d = {x.mission_id: x for x in state.mission_preflights}
        self.assertIn("OPERATOR_SUSPENDED", d["primary"].blockers)
        self.assertIn("EXTERNAL_HEALTH_NOT_VERIFIED", d["external"].blockers)

    def test_bounded_page_always_reports_omitted_ready_missions(self):
        with StateStore(self.path) as store:
            store.add_missions([
                Mission(f"f-{n:02}", REPO) for n in range(23)])
        payload, state = self.read()
        self.assertEqual(payload["preflight_total_ready"], 24)
        self.assertEqual(len(state.mission_preflights), 20)
        self.assertEqual(
            [p.mission_id for p in state.mission_preflights],
            [t.task_id for t in state.tasks if t.state == "READY"][:20])

    def test_wire_rejects_forged_preflight_and_ambiguous_status(self):
        self.grant()
        payload, _ = self.read()
        cases = (
            ("ready-with-fake-permission", lambda p: p["mission_preflights"][0].update(
                permission="MISSING_OR_EXPIRED")),
            ("fake-exec", lambda p: p["mission_preflights"][0].update(
                executes_actions=True)),
            ("fake-benchmark", lambda p: p["mission_preflights"][0].update(
                model_benchmark="PASS")),
            ("missing-row", lambda p: p.update(mission_preflights=[])),
            ("missing-operator", lambda p: p.pop("paused")),
            ("fake-ready-count", lambda p: p.update(preflight_total_ready=99)),
        )
        for name, corrupt in cases:
            with self.subTest(name=name):
                bad = deepcopy(payload)
                corrupt(bad)
                with self.assertRaises(ControlCenterStateError):
                    consume_dashboard_snapshot(
                        json.dumps(bad).encode(),
                        trusted_engine_id="eng-f02",
                        expected_sequence=bad["sequence"], now_epoch=NOW)

    def test_v6_primary_snapshot_remains_supported(self):
        payload, _ = self.read()
        payload["schema_version"] = 6
        del payload["mission_preflights"]
        del payload["preflight_total_ready"]
        state = consume_dashboard_snapshot(
            json.dumps(payload).encode(), trusted_engine_id="eng-f02",
            expected_sequence=payload["sequence"], now_epoch=NOW)
        self.assertIsNotNone(state.mission_preflight)
        self.assertEqual(state.mission_preflights, ())

    def test_page_and_primary_cannot_disagree(self):
        payload, _ = self.read()
        payload["mission_preflight"] = None
        with self.assertRaises(ControlCenterStateError):
            consume_dashboard_snapshot(
                json.dumps(payload).encode(), trusted_engine_id="eng-f02",
                expected_sequence=payload["sequence"], now_epoch=NOW)

    def test_qt_tab_displays_blocker_without_launch_capability(self):
        from PySide6.QtWidgets import QApplication
        from holding.control_center_ui import ControlCenterWindow
        app = QApplication.instance() or QApplication([])
        _, state = self.read()
        window = ControlCenterWindow(trusted_engine_id="eng-f02")
        try:
            self.assertTrue(window.render_state(state))
            self.assertEqual(window.preflights.rowCount(), 1)
            self.assertIn("Manquant", window.preflights.item(0, 2).text())
            self.assertIn("NO_ACTIVE_SCOPED_GRANT",
                          window.preflights.item(0, 3).text())
            self.assertIn("1 / 1", window.preflight_summary.text())
            self.assertFalse(hasattr(window, "start_preflight_button"))
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()

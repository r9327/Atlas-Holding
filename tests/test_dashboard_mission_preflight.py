"""Autonomie V2 F02: authenticated end-to-end preflight from SQLite to UI."""
from __future__ import annotations

import json
import os
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
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "holding.sqlite"
        with StateStore(self.db_path) as store:
            store.add_missions([Mission("alpha", REPO)])
            store.db.execute(
                "UPDATE missions SET updated_at=? WHERE id='alpha'", (NOW - 10,))

    def project(self):
        with ControlPlane(self.db_path) as control:
            before = control.store.db.total_changes
            raw = project_dashboard_snapshot(control, engine_id="engine-1",
                                             now_epoch=NOW)
            self.assertEqual(control.store.db.total_changes, before)
        payload = json.loads(raw)
        return payload, self.consume(payload)

    @staticmethod
    def consume(payload):
        raw = json.dumps(payload, ensure_ascii=True).encode("ascii")
        return consume_dashboard_snapshot(
            raw, trusted_engine_id="engine-1",
            expected_sequence=payload["sequence"], now_epoch=NOW)

    def grant(self):
        with StateStore(self.db_path) as store:
            store.grant_for_onboarding(StandingGrant(
                REPO, frozenset({"code"}), NOW + 100))

    def test_missing_grant_is_visible_without_permission_escalation(self):
        payload, state = self.project()
        self.assertEqual(payload["schema_version"], 7)
        self.assertEqual(state.mission_preflight.status, "BLOCKED")
        self.assertEqual(state.mission_preflight.permission, "MISSING_OR_EXPIRED")
        self.assertEqual(state.mission_preflight.blockers,
                         ("NO_ACTIVE_SCOPED_GRANT",))
        self.assertEqual(payload["mission_preflight"]["worker_validation"],
                         "NOT_RUN")
        self.assertFalse(payload["mission_preflight"]["executes_actions"])

    def test_scoped_grant_and_dependency_are_distinct(self):
        with StateStore(self.db_path) as store:
            store.add_missions([Mission("beta", REPO, dependencies=("alpha",),
                                        priority=50)])
            store.db.execute("UPDATE missions SET updated_at=? WHERE id='beta'",
                             (NOW - 10,))
        self.grant()
        payload, state = self.project()
        self.assertEqual(state.mission_preflight.mission_id, "beta")
        self.assertEqual(state.mission_preflight.status,
                         "WAITING_FOR_DEPENDENCIES")
        self.assertEqual(state.mission_preflight.waiting_for, ("alpha",))
        self.assertEqual(payload["mission_preflight"]["permission"],
                         "SCOPED_GRANT_PRESENT")
        with StateStore(self.db_path) as store:
            store.db.execute(
                "UPDATE missions SET state='DONE',candidate_sha=?,evidence_sha=? "
                "WHERE id='alpha'", ("a" * 40, "a" * 40))
        _, state = self.project()
        self.assertEqual(state.mission_preflight.status, "PRECHECK_PASSED")
        self.assertEqual(state.mission_preflight.waiting_for, ())

    def test_signed_view_rejects_claims_of_model_worker_and_execution(self):
        self.grant()
        payload, state = self.project()
        self.assertEqual(state.mission_preflight.status, "PRECHECK_PASSED")
        variants = (
            {"executes_actions": True},
            {"worker_validation": "PASS"},
            {"model_benchmark": "PASS"},
            {"mission_id": "not-present"},
            {"status": "READY"},
            {"permission": "MISSING_OR_EXPIRED"},
            {"repository": "other"},
        )
        for patch in variants:
            forged = json.loads(json.dumps(payload))
            forged["mission_preflight"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ControlCenterStateError):
                self.consume(forged)

    def test_v5_snapshot_without_preflight_remains_readable(self):
        self.grant()
        payload, _ = self.project()
        payload["schema_version"] = 5
        del payload["mission_preflight"]
        del payload["mission_preflights"]
        del payload["preflight_total_ready"]
        state = self.consume(payload)
        self.assertIsNone(state.mission_preflight)

    def test_no_ready_missions_gives_no_preflight(self):
        with StateStore(self.db_path) as store:
            store.db.execute("UPDATE missions SET state='BLOCKED'")
        payload, state = self.project()
        self.assertIsNone(payload["mission_preflight"])
        self.assertIsNone(state.mission_preflight)


class PreflightQtDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_display_and_disconnection_never_claim_authorization(self):
        from holding.control_center_ui import ControlCenterWindow
        from holding.control_center_state import DashboardState, MissionPreflightView
        state = DashboardState(
            engine_id="engine-1", sequence=1, generated_at=NOW,
            network="UNKNOWN", pending_external=0, tasks=(), agents=(),
            plugins=(), logs=(), next_log_cursor=None,
            mission_preflight=MissionPreflightView(
                "alpha", "BLOCKED", ("NO_ACTIVE_SCOPED_GRANT",), (),
                "MISSING_OR_EXPIRED"),
        )
        window = ControlCenterWindow(trusted_engine_id="engine-1")
        try:
            self.assertTrue(window.render_state(state))
            self.assertIn("autorisation absente", window.mission_preflight_status.text())
            window.mark_local_hub_unverified()
            self.assertIn("non vérifié", window.mission_preflight_status.text())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()

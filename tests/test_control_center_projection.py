import json
from pathlib import Path
import tempfile
import unittest

from holding.autonomy import Mission
from holding.control import ControlPlane
from holding.control_center_projection import project_dashboard_snapshot
from holding.control_center_state import consume_dashboard_snapshot
from holding.schema import SIGNED_PROOF_SCHEMA, apply_schema
from holding.state_store import StateStore


NOW = 1_800_000_000
SHA = "a" * 40
DIGEST = "b" * 64


class ControlCenterProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "holding.sqlite"
        with StateStore(self.path) as store:
            store.add_missions([Mission("first", "r9327/Dof-Atlas")])
            store.db.execute("UPDATE missions SET updated_at=? WHERE id='first'", (NOW - 9,))

    def project(self):
        with ControlPlane(self.path) as control:
            raw = project_dashboard_snapshot(
                control, engine_id="engine-1", now_epoch=NOW)
        payload = json.loads(raw)
        return payload, consume_dashboard_snapshot(
            raw, trusted_engine_id="engine-1",
            expected_sequence=payload["sequence"], now_epoch=NOW,
        )

    def test_real_ready_mission_has_no_fake_sha_or_progress(self):
        payload, state = self.project()
        task = state.tasks[0]
        self.assertEqual(task.state, "READY")
        self.assertEqual(task.progress, "WAITING")
        self.assertIsNone(task.candidate_sha)
        self.assertIsNone(task.completion_evidence)
        self.assertEqual(task.elapsed_seconds, 9)
        self.assertEqual(payload["network"], "UNKNOWN")

    def test_unproved_durable_done_is_visibly_not_verified(self):
        with StateStore(self.path) as store:
            store.db.execute(
                "UPDATE missions SET state='DONE',candidate_sha=?,evidence_sha=? "
                "WHERE id='first'", (SHA, SHA))
        _, state = self.project()
        self.assertEqual(state.tasks[0].state, "DONE_UNVERIFIED")
        self.assertEqual(state.tasks[0].progress, "BLOCKED")
        self.assertIsNone(state.tasks[0].completion_evidence)

    def test_recovery_before_commit_projects_without_sha(self):
        with StateStore(self.path) as store:
            store.db.execute(
                "UPDATE missions SET state='RECOVERY_REQUIRED' WHERE id='first'")
        _, state = self.project()
        self.assertEqual(state.tasks[0].state, "RECOVERY_REQUIRED")
        self.assertIsNone(state.tasks[0].candidate_sha)

    def test_verified_signed_proof_projects_exact_evidence(self):
        with StateStore(self.path) as store:
            apply_schema(store.db, SIGNED_PROOF_SCHEMA)
            store.db.execute(
                "UPDATE missions SET state='DONE',candidate_sha=?,evidence_sha=? "
                "WHERE id='first'", (SHA, SHA))
            store.db.execute(
                "INSERT INTO signed_proofs VALUES(?,?,?,?,?)",
                ("first", SHA, "PASS", "ci.json", DIGEST))
        _, state = self.project()
        task = state.tasks[0]
        self.assertEqual(task.state, "DONE")
        self.assertEqual(task.completion_evidence.kind, "CI")
        self.assertEqual(task.completion_evidence.digest, DIGEST)

    def test_sync_proof_cannot_be_borrowed_by_unrelated_mission_same_sha(self):
        from holding.schema import SYNC_STATE_SCHEMA
        with StateStore(self.path) as store:
            apply_schema(store.db, SYNC_STATE_SCHEMA)
            store.add_missions([
                Mission("synced", "r9327/Dof-Atlas", operation="push",
                        external_required=True),
                Mission("unrelated", "r9327/Dof-Atlas", operation="push",
                        external_required=True),
            ])
            store.db.execute(
                "UPDATE missions SET state='DONE',candidate_sha=?,evidence_sha=?,"
                "updated_at=? WHERE id IN ('synced','unrelated')",
                (SHA, SHA, NOW - 5))
            key = "c" * 64
            store.db.execute(
                "INSERT INTO github_sync_attempts "
                "(operation,repo,branch,candidate_sha,attempt,status,"
                "retryable,idempotency_key,updated_epoch) "
                "VALUES('github.push','r9327/Dof-Atlas','holding/code-1',?,"
                "1,'SUCCEEDED',0,?,?)", (SHA, key, NOW - 2))
            store._event("synced", "RUNNING_TO_DONE",
                         "verified sync:github.push:" + key)
            # A different DONE record cannot inherit the same successful push.
            store._event("unrelated", "RUNNING_TO_DONE",
                         "verified sync:github.merge:" + key)
        payload, state = self.project()
        by_id = {task.task_id: task for task in state.tasks}
        self.assertEqual(by_id["synced"].state, "DONE")
        self.assertEqual(by_id["synced"].completion_evidence.kind, "SYNC")
        self.assertEqual(by_id["synced"].completion_evidence.scope, "SCOPED")
        self.assertEqual(by_id["unrelated"].state, "DONE_UNVERIFIED")
        self.assertIsNone(by_id["unrelated"].completion_evidence)
        self.assertNotIn("verified sync:", json.dumps(payload))

    def test_event_details_are_not_exposed_to_ui(self):
        with StateStore(self.path) as store:
            store._event("first", "MISSION_BLOCKED", "token=must-not-leak")
        payload, state = self.project()
        self.assertNotIn("must-not-leak", json.dumps(payload))
        self.assertEqual(state.logs[-1].message, "MISSION_BLOCKED")


if __name__ == "__main__":
    unittest.main()

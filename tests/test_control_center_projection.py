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

    def test_durable_pause_stop_are_projected_atomically(self):
        payload, state = self.project()
        self.assertEqual(payload["schema_version"], 7)
        self.assertEqual(payload["services"], [])
        self.assertIs(state.paused, False)
        self.assertIs(state.stop_requested, False)
        with ControlPlane(self.path) as control:
            control.pause()
        payload, state = self.project()
        self.assertIs(state.paused, True)
        self.assertIs(state.stop_requested, False)
        with ControlPlane(self.path) as control:
            control.request_stop()
        payload, state = self.project()
        self.assertIs(state.paused, True)
        self.assertIs(state.stop_requested, True)

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

    def test_external_services_are_real_sqlite_observations_not_plugins(self):
        from holding.schema import SYNC_STATE_SCHEMA
        with StateStore(self.path) as store:
            apply_schema(store.db, SYNC_STATE_SCHEMA)
            store.db.execute(
                "INSERT INTO external_service_health "
                "(service,status,observed_at,consecutive_successes,"
                "consecutive_failures,retry_attempts,retry_not_before,reason,"
                "updated_epoch) VALUES (?,?,?,?,?,?,?,?,?)",
                ("github", "ONLINE", NOW - 12, 3, 0, 0, None,
                 "access-token=NEVER-EXPOSE", NOW - 12),
            )
            store.db.execute(
                "INSERT INTO external_service_health "
                "(service,status,observed_at,consecutive_successes,"
                "consecutive_failures,retry_attempts,retry_not_before,reason,"
                "updated_epoch) VALUES (?,?,?,?,?,?,?,?,?)",
                ("firecrawl", "OFFLINE", NOW - 901, 0, 2, 1, NOW + 30,
                 "Authorization header leak", NOW - 901),
            )
        payload, state = self.project()
        self.assertEqual(payload["schema_version"], 7)
        by_name = {service.name: service for service in state.services}
        self.assertEqual(by_name["github"].status, "ONLINE")
        self.assertEqual(by_name["github"].age_seconds, 12)
        self.assertEqual(by_name["firecrawl"].status, "STALE")
        self.assertEqual(by_name["firecrawl"].age_seconds, 901)
        self.assertEqual(state.network, "DEGRADED")
        self.assertEqual(state.plugins, ())  # Service != installed MCP plugin
        self.assertNotIn("NEVER-EXPOSE", json.dumps(payload))
        self.assertNotIn("Authorization header", json.dumps(payload))

    def test_expired_single_online_service_never_means_online_stable(self):
        from holding.schema import SYNC_STATE_SCHEMA
        with StateStore(self.path) as store:
            apply_schema(store.db, SYNC_STATE_SCHEMA)
            store.db.execute(
                "INSERT INTO external_service_health "
                "(service,status,observed_at,consecutive_successes,"
                "consecutive_failures,retry_attempts,retry_not_before,reason,"
                "updated_epoch) VALUES (?,?,?,?,?,?,?,?,?)",
                ("github", "ONLINE", NOW - 301, 2, 0, 0, None, None, NOW - 301),
            )
        _, state = self.project()
        self.assertEqual(state.services[0].status, "STALE")
        self.assertNotEqual(state.network, "ONLINE_STABLE")

    def test_recorded_model_usage_is_historical_not_live_agent(self):
        from holding.schema import MODEL_SCHEMA_V1
        with StateStore(self.path) as store:
            apply_schema(store.db, MODEL_SCHEMA_V1)
            store.db.execute(
                "INSERT INTO model_profiles VALUES (?,?,?,?,?,?,?,?,?)",
                ("c" * 64, "qwen2.5-coder:7b", "Ollama", "Q4",
                 8192, 100_000_000, 0, "d" * 64, NOW - 60),
            )
            store.db.execute(
                "INSERT INTO mission_model_usage "
                "(mission_id,role,model_digest_sha256,candidate_sha,observed_at)"
                " VALUES(?,?,?,?,?)",
                ("first", "coder", "c" * 64, "", NOW - 18),
            )
        payload, state = self.project()
        self.assertEqual(payload["schema_version"], 7)
        self.assertEqual(len(state.model_history), 1)
        usage = state.model_history[0]
        self.assertEqual(usage.role, "CODER")
        self.assertEqual(usage.task_id, "first")
        self.assertEqual(usage.model_name, "qwen2.5-coder:7b")
        self.assertEqual(usage.model_digest, "c" * 64)
        self.assertEqual(usage.benchmark_digest, "d" * 64)
        self.assertEqual(usage.observed_at, NOW - 18)
        self.assertEqual(state.agents, ())
        self.assertEqual(state.plugins, ())
        self.assertNotIn("RUNNING", json.dumps(payload))

    def test_model_usage_is_scoped_to_visible_missions_and_bounded(self):
        from holding.schema import MODEL_SCHEMA_V1
        with StateStore(self.path) as store:
            apply_schema(store.db, MODEL_SCHEMA_V1)
            store.add_missions([Mission("hidden", "r9327/Dof-Atlas")])
            store.db.execute(
                "INSERT INTO model_profiles VALUES (?,?,?,?,?,?,?,?,?)",
                ("f" * 64, "qwen", "Ollama", "Q4",
                 4096, 1_000_000, 0, "e" * 64, NOW - 40),
            )
            for index in range(31):
                store.db.execute(
                    "INSERT INTO mission_model_usage "
                    "(mission_id,role,model_digest_sha256,candidate_sha,observed_at)"
                    " VALUES(?,?,?,?,?)",
                    ("first" if index % 2 else "hidden", "coder",
                     "f" * 64, "", NOW - index),
                )
        _, state = self.project()
        self.assertEqual(len(state.model_history), 20)
        self.assertEqual(
            [row.observed_at for row in state.model_history],
            sorted([row.observed_at for row in state.model_history], reverse=True),
        )
        self.assertTrue(all(row.model_digest == "f" * 64
                            for row in state.model_history))

    def test_real_internal_hub_journal_is_projected_with_exact_provenance(self):
        with StateStore(self.path) as store:
            store.db.execute(
                "CREATE TABLE mcp_hub_observations ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                "state TEXT,recorded_epoch INTEGER,pid INTEGER,"
                "source_sha256 TEXT,protocol_version TEXT)"
            )
            store.db.execute(
                "INSERT INTO mcp_hub_observations "
                "(state,recorded_epoch,pid,source_sha256,protocol_version)"
                " VALUES(?,?,?,?,?)",
                ("RESPONSIVE", NOW - 12, 4242, "c" * 64, "2026-07-28"),
            )
        payload, state = self.project()
        self.assertEqual(payload["schema_version"], 7)
        self.assertEqual(state.hub.state, "RESPONSIVE")
        self.assertEqual(state.hub.protocol_version, "2026-07-28")
        self.assertEqual(state.hub.age_seconds, 12)
        self.assertEqual(state.hub.source_sha256, "c" * 64)
        self.assertEqual(state.plugins, ())
        self.assertNotEqual(payload["hub"]["state"], "ACTIVE")

    def test_old_or_invalid_hub_observation_cannot_claim_live_protocol(self):
        from holding.control_center_projection import DashboardProjectionError
        with StateStore(self.path) as store:
            store.db.execute(
                "CREATE TABLE mcp_hub_observations ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                "state TEXT,recorded_epoch INTEGER,pid INTEGER,"
                "source_sha256 TEXT,protocol_version TEXT)"
            )
            store.db.execute(
                "INSERT INTO mcp_hub_observations "
                "(state,recorded_epoch,pid,source_sha256,protocol_version)"
                " VALUES(?,?,?,?,?)",
                ("RESPONSIVE", NOW - 31, 4242, "d" * 64, "2026-07-28"),
            )
        _, stale = self.project()
        self.assertEqual(stale.hub.state, "STALE")
        self.assertIsNone(stale.hub.protocol_version)
        with StateStore(self.path) as store:
            store.db.execute(
                "UPDATE mcp_hub_observations SET source_sha256='forged'")
        with self.assertRaises(DashboardProjectionError):
            self.project()

    def test_event_details_are_not_exposed_to_ui(self):
        with StateStore(self.path) as store:
            store._event("first", "MISSION_BLOCKED", "token=must-not-leak")
        payload, state = self.project()
        self.assertNotIn("must-not-leak", json.dumps(payload))
        self.assertEqual(state.logs[-1].message, "MISSION_BLOCKED")


if __name__ == "__main__":
    unittest.main()

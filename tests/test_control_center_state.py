import json
import unittest

from holding.control_center_state import (
    ControlCenterStateError, MAX_LOG_PAGE, MAX_SNAPSHOT_AGE_SECONDS,
    consume_dashboard_snapshot,
)


SHA = "a" * 40
DIGEST = "b" * 64
NOW = 1_800_000_020


def snapshot():
    return {
        "schema_version": 1,
        "engine_id": "engine-1",
        "sequence": 42,
        "generated_at": 1_800_000_000,
        "network": "CI_PENDING",
        "pending_external": 1,
        "tasks": [{
            "id": "task-1", "title": "Verify candidate", "state": "DONE",
            "phase": "CERTIFICATION", "progress": "COMPLETED",
            "candidate_sha": SHA, "elapsed_seconds": 12, "attempts": 1,
            "completion_evidence": {
                "kind": "CI", "outcome": "SUCCESS", "scope": "FULL",
                "sha": SHA, "digest": DIGEST,
                "source": "https://github.com/r9327/Atlas-Holding/actions/runs/1",
            },
        }],
        "agents": [{
            "id": "auditor-1", "role": "AUDITOR", "status": "IDLE",
            "objective": "Await exact evidence", "task_id": "task-1",
            "model_profile": "Meilleur compromis qualite / temps",
            "last_event_id": "event-42",
        }],
        "plugins": [{
            "name": "github", "version": "1.0", "state": "ACTIVE",
            "mode": "ONLINE", "permission": "READ", "quota_remaining": 9,
            "cache_generated_at": None,
        }],
        "log_page": {"items": [{
            "sequence": 41, "category": "CI", "message": "CI exact SHA ok",
            "task_id": "task-1", "plugin_name": "github",
            "evidence_digest": DIGEST,
        }], "next_cursor": "before:41"},
    }


def consume(payload):
    return consume_dashboard_snapshot(
        json.dumps(payload).encode("utf-8"), trusted_engine_id="engine-1",
        expected_sequence=42, now_epoch=NOW,
    )


class ControlCenterStateTests(unittest.TestCase):
    def test_valid_snapshot_becomes_immutable_display_state(self):
        state = consume(snapshot())
        self.assertEqual(state.tasks[0].completion_evidence.sha, SHA)
        self.assertEqual(state.network, "CI_PENDING")
        with self.assertRaises(AttributeError):
            state.sequence = 43

    def test_v2_operator_flags_are_strict_and_v1_stays_compatible(self):
        self.assertIsNone(consume(snapshot()).paused)
        self.assertIsNone(consume(snapshot()).stop_requested)
        newer = snapshot()
        newer.update(schema_version=2, paused=True, stop_requested=False)
        state = consume(newer)
        self.assertIs(state.paused, True)
        self.assertIs(state.stop_requested, False)
        for changes in (
                {"paused": 1},
                {"stop_requested": "false"},
                {"paused": None},
                {"schema_version": 3},
                {"surplus": "not allowed"}):
            invalid = dict(newer)
            invalid.update(changes)
            with self.subTest(changes=changes), self.assertRaises(
                    ControlCenterStateError):
                consume(invalid)
        missing = dict(newer)
        del missing["paused"]
        with self.assertRaises(ControlCenterStateError):
            consume(missing)

    def test_v3_services_are_strict_and_v2_stays_compatible(self):
        legacy = snapshot()
        legacy.update(schema_version=2, paused=False, stop_requested=False)
        self.assertEqual(consume(legacy).services, ())
        updated = dict(legacy, schema_version=3, services=[
            {"name": "github", "status": "ONLINE", "age_seconds": 18},
            {"name": "firecrawl", "status": "STALE", "age_seconds": 399},
        ])
        state = consume(updated)
        self.assertEqual(len(state.services), 2)
        self.assertEqual(state.services[1].status, "STALE")
        cases = [
            [updated["services"][0]] * 2,
            [{"name": "github", "status": "ACTIVE", "age_seconds": 1}],
            [{"name": "github", "status": "ONLINE", "age_seconds": 301}],
            [{"name": "github", "status": "STALE", "age_seconds": 299}],
            [{"name": "github", "status": "UNKNOWN", "age_seconds": 0}],
            [{"name": "github", "status": "ONLINE", "age_seconds": True}],
            [{"name": "rogue", "status": "ONLINE", "age_seconds": 1}],
            [{"name": "github", "status": "ONLINE", "age_seconds": 1,
              "installed": True}],
        ]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ControlCenterStateError):
                    consume(dict(updated, services=invalid))
        without_services = dict(updated)
        del without_services["services"]
        with self.assertRaises(ControlCenterStateError):
            consume(without_services)
        with self.assertRaises(ControlCenterStateError):
            consume(dict(updated, services=[{"name": "github",
                                              "status": "ONLINE",
                                              "age_seconds": 1}] * 4))

    def test_v4_model_history_is_exact_digest_bounded_and_scoped(self):
        payload = snapshot()
        payload.update(
            schema_version=4, paused=False, stop_requested=False,
            services=[], model_history=[{
                "role": "CODER", "task_id": "task-1",
                "model_name": "qwen2.5-coder:7b",
                "model_digest": "c" * 64,
                "benchmark_digest": "d" * 64,
                "observed_at": NOW - 10,
            }],
        )
        state = consume(payload)
        self.assertEqual(len(state.model_history), 1)
        self.assertEqual(state.model_history[0].model_name, "qwen2.5-coder:7b")
        self.assertEqual(state.model_history[0].role, "CODER")
        self.assertEqual(len(state.agents), 1)  # Source fixture only, not inferred

        expected = payload["model_history"][0]
        for change in (
            {"role": "LEAD"}, {"role": ["CODER"]},
            {"task_id": "other-task"}, {"model_digest": "a" * 40},
            {"benchmark_digest": True}, {"observed_at": NOW + 10},
            {"model_name": ""}, {"installed": True},
        ):
            invalid = dict(payload)
            invalid["model_history"] = [dict(expected, **change)]
            with self.subTest(change=change):
                with self.assertRaises(ControlCenterStateError):
                    consume(invalid)
        for broken in (None, [expected] * 21):
            with self.subTest(broken=broken is None):
                with self.assertRaises(ControlCenterStateError):
                    consume(dict(payload, model_history=broken))
        missing = dict(payload)
        del missing["model_history"]
        with self.assertRaises(ControlCenterStateError):
            consume(missing)
        with self.assertRaises(ControlCenterStateError):
            consume(dict(payload, model_history=[
                dict(expected, observed_at=NOW - 11),
                dict(expected, observed_at=NOW - 9),
            ]))

    def test_done_without_evidence_is_impossible(self):
        payload = snapshot()
        payload["tasks"][0]["completion_evidence"] = None
        with self.assertRaisesRegex(ControlCenterStateError, "DONE"):
            consume(payload)

    def test_pre_candidate_task_has_no_invented_sha(self):
        payload = snapshot()
        payload["tasks"][0].update({
            "state": "READY", "phase": "CODING", "progress": "WAITING",
            "candidate_sha": None, "completion_evidence": None,
        })
        state = consume(payload)
        self.assertIsNone(state.tasks[0].candidate_sha)

    def test_verification_states_require_real_candidate_sha(self):
        for state in ("QA_PENDING", "DONE_UNVERIFIED"):
            payload = snapshot()
            payload["tasks"][0].update({
                "state": state,
                "progress": "WAITING" if state == "QA_PENDING" else "BLOCKED",
                "candidate_sha": None,
                "completion_evidence": None,
            })
            with self.subTest(state=state), self.assertRaisesRegex(
                    ControlCenterStateError, "requires a candidate SHA"):
                consume(payload)

    def test_recovery_before_first_commit_has_no_fake_sha(self):
        payload = snapshot()
        payload["tasks"][0].update({
            "state": "RECOVERY_REQUIRED", "phase": "EXTERNAL_WAIT",
            "progress": "BLOCKED", "candidate_sha": None,
            "completion_evidence": None,
        })
        state = consume(payload)
        self.assertIsNone(state.tasks[0].candidate_sha)

    def test_non_done_task_cannot_borrow_completion_evidence(self):
        payload = snapshot()
        payload["tasks"][0].update({
            "state": "QA_PENDING", "phase": "TESTS", "progress": "WAITING",
        })
        with self.assertRaisesRegex(ControlCenterStateError, "Non-DONE"):
            consume(payload)

    def test_local_doctor_and_scoped_tests_cannot_be_final_done(self):
        for kind, scope in (("DOCTOR", "FULL"), ("TEST", "SCOPED"),
                            ("CI", "SCOPED")):
            payload = snapshot()
            payload["tasks"][0]["completion_evidence"].update({
                "kind": kind, "scope": scope})
            with self.subTest(kind=kind, scope=scope):
                with self.assertRaises(ControlCenterStateError):
                    consume(payload)

    def test_sync_success_is_separate_from_full_quality_certification(self):
        payload = snapshot()
        task = payload["tasks"][0]
        task["title"] = "push-1 · push"
        task["completion_evidence"].update({
            "kind": "SYNC", "scope": "SCOPED",
            "source": "github_sync_attempts", "outcome": "SUCCESS"})
        self.assertEqual(consume(payload).tasks[0].completion_evidence.kind, "SYNC")
        for change in (
                {"title": "code-1 · code"},
                {"source": "self-declared"},
                {"scope": "FULL"},
                {"outcome": "PASS"}):
            candidate = snapshot()
            entry = candidate["tasks"][0]
            entry["title"] = "push-1 · push"
            entry["completion_evidence"].update({
                "kind": "SYNC", "scope": "SCOPED",
                "source": "github_sync_attempts", "outcome": "SUCCESS"})
            if "title" in change:
                entry["title"] = change["title"]
            else:
                entry["completion_evidence"].update(change)
            with self.subTest(change=change), self.assertRaises(ControlCenterStateError):
                consume(candidate)

    def test_evidence_must_match_candidate_sha(self):
        payload = snapshot()
        payload["tasks"][0]["completion_evidence"]["sha"] = "c" * 40
        with self.assertRaisesRegex(ControlCenterStateError, "another SHA"):
            consume(payload)

    def test_unproved_scores_and_percentages_are_rejected(self):
        payload = snapshot()
        payload["tasks"][0]["progress_percent"] = 100
        with self.assertRaisesRegex(ControlCenterStateError, "object shape"):
            consume(payload)

    def test_stale_sequence_or_wrong_engine_is_rejected(self):
        payload = snapshot()
        payload["sequence"] = 41
        with self.assertRaisesRegex(ControlCenterStateError, "sequence"):
            consume(payload)

    def test_stale_future_or_ambiguous_snapshot_is_rejected(self):
        for generated_at in (
                NOW - MAX_SNAPSHOT_AGE_SECONDS - 1, NOW + 6):
            payload = snapshot()
            payload["generated_at"] = generated_at
            with self.subTest(generated_at=generated_at), self.assertRaisesRegex(
                    ControlCenterStateError, "stale or from the future"):
                consume(payload)
        raw = json.dumps(snapshot()).encode("utf-8")
        raw = raw.replace(b'"schema_version": 1',
                          b'"schema_version": 1, "schema_version": 1', 1)
        with self.assertRaisesRegex(ControlCenterStateError, "duplicate JSON"):
            consume_dashboard_snapshot(
                raw, trusted_engine_id="engine-1", expected_sequence=42,
                now_epoch=NOW,
            )
        payload = snapshot()
        payload["engine_id"] = "other-engine"
        with self.assertRaisesRegex(ControlCenterStateError, "identity"):
            consume(payload)

    def test_task_state_and_progress_must_agree(self):
        payload = snapshot()
        payload["tasks"][0]["state"] = "RUNNING"
        with self.assertRaisesRegex(ControlCenterStateError, "contradicts"):
            consume(payload)

    def test_running_agent_requires_known_task(self):
        payload = snapshot()
        payload["agents"][0]["status"] = "RUNNING"
        payload["agents"][0]["task_id"] = "unknown"
        with self.assertRaisesRegex(ControlCenterStateError, "unknown task"):
            consume(payload)

    def test_plugin_permissions_are_read_or_cache_only(self):
        payload = snapshot()
        payload["plugins"][0]["permission"] = "SHELL"
        with self.assertRaisesRegex(ControlCenterStateError, "permission"):
            consume(payload)

    def test_logs_are_bounded_ordered_and_not_from_future(self):
        payload = snapshot()
        payload["log_page"]["items"] *= MAX_LOG_PAGE + 1
        with self.assertRaisesRegex(ControlCenterStateError, "bounded log"):
            consume(payload)
        payload = snapshot()
        payload["log_page"]["items"][0]["sequence"] = 43
        with self.assertRaisesRegex(ControlCenterStateError, "future"):
            consume(payload)

    def test_boolean_is_not_accepted_as_numeric_evidence(self):
        payload = snapshot()
        payload["tasks"][0]["attempts"] = True
        with self.assertRaisesRegex(ControlCenterStateError, "attempt"):
            consume(payload)
        payload = snapshot()
        payload["schema_version"] = True
        with self.assertRaisesRegex(ControlCenterStateError, "identity"):
            consume(payload)


if __name__ == "__main__":
    unittest.main()

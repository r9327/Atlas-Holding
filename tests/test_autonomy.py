import unittest
from holding.autonomy import Mission, StandingGrant, choose_next

NOW = 12345
REPO = "r9327/Dof-Atlas"
GRANT = StandingGrant(REPO, frozenset({"code", "push", "ready_for_review", "merge"}), NOW + 100)


class SchedulerTests(unittest.TestCase):
    def choose(self, jobs, grants=(GRANT,), online=True):
        return choose_next(jobs, grants, now_epoch=NOW, online=online)

    def test_independent_ready_task_continues_while_decision_pending(self):
        jobs = [Mission("blocked", REPO, product_decision_required=True, priority=10),
                Mission("fine", REPO, priority=1)]
        self.assertEqual(self.choose(jobs).mission_id, "fine")

    def test_unavailable_network_does_not_block_local_work(self):
        jobs = [Mission("web", REPO, operation="push", external_required=True, priority=5),
                Mission("local", REPO)]
        self.assertEqual(self.choose(jobs, online=False).mission_id, "local")

    def test_no_network_when_only_remote_task(self):
        self.assertEqual(self.choose([Mission("web", REPO, operation="push",
                                              external_required=True)], online=False).action,
                         "WAIT_FOR_NETWORK")

    def test_expired_grant_never_authorizes(self):
        old = StandingGrant(REPO, frozenset({"code"}), NOW)
        self.assertEqual(self.choose([Mission("a", REPO)], grants=(old,)).action,
                         "REQUIRES_DECISION")

    def test_wrong_repository_grant_never_authorizes(self):
        other = StandingGrant("r9327/Atlas-Holding", frozenset({"code"}), NOW + 100)
        self.assertEqual(self.choose([Mission("a", REPO)], grants=(other,)).action,
                         "REQUIRES_DECISION")

    def test_forbidden_operation_cannot_be_granted(self):
        allgrant = StandingGrant(REPO, frozenset({"disable_doctor"}), NOW + 100)
        self.assertEqual(self.choose([Mission("a", REPO, operation="disable_doctor")],
                                     (allgrant,)).action, "BLOCKED")

    def test_unknown_operations_fail_closed(self):
        self.assertEqual(self.choose([Mission("a", REPO, operation="new_magic_tool")]).action,
                         "BLOCKED")

    def test_retry_limit_does_not_prevent_other_tasks(self):
        jobs = [Mission("loop", REPO, priority=10, failures_without_progress=3),
                Mission("good", REPO)]
        self.assertEqual(self.choose(jobs).mission_id, "good")

    def test_missing_dependency_detected(self):
        self.assertEqual(self.choose([Mission("a", REPO, dependencies=("missing",))]).action,
                         "BLOCKED")

    def test_duplicate_ids_detected(self):
        self.assertEqual(self.choose([Mission("a", REPO), Mission("a", REPO)]).action,
                         "BLOCKED")

    def test_evidence_must_match_sha(self):
        done = Mission("done", REPO, state="DONE", candidate_sha="123", evidence_sha="456")
        self.assertEqual(self.choose([done, Mission("next", REPO, dependencies=("done",))]).action,
                         "BLOCKED")

    def test_dependency_unblocks_after_proven_completion(self):
        done = Mission("done", REPO, state="DONE", candidate_sha="123", evidence_sha="123")
        self.assertEqual(self.choose([done, Mission("next", REPO, dependencies=("done",))]).mission_id,
                         "next")

    def test_complete_when_all_done(self):
        done = Mission("done", REPO, state="DONE", candidate_sha="123", evidence_sha="123")
        self.assertEqual(self.choose([done]).action, "COMPLETE")

    def test_empty_queue_complete(self):
        self.assertEqual(self.choose([]).action, "COMPLETE")

    def test_no_permissions_means_no_mutation(self):
        self.assertEqual(self.choose([Mission("a", REPO)], ()).action, "REQUIRES_DECISION")

    def test_paused_active_task_is_not_concurrently_dispatched(self):
        self.assertEqual(self.choose([Mission("running", REPO, state="RUNNING")]).action, "WAIT")

    def test_no_second_writer_when_one_running(self):
        jobs = [Mission("active", REPO, state="RUNNING"), Mission("ready", REPO)]
        self.assertEqual(self.choose(jobs).action, "WAIT")

    def test_dependency_cycle_blocks(self):
        jobs = [Mission("a", REPO, dependencies=("b",)),
                Mission("b", REPO, dependencies=("a",))]
        self.assertEqual(self.choose(jobs).action, "BLOCKED")


if __name__ == "__main__":
    unittest.main()

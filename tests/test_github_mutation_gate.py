import hashlib
import json
import unittest

from holding.github_full_evidence import FullEvidence, GitHubEvidenceError
from holding.github_mutation_gate import prepare_github_mutation
from holding.git_sync_gate import GitPushState, PushDisposition


REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
NOW = 1_800_000_000
BRANCH = "holding/lot-1"


def authorization(operation):
    value = {
        "schema_version": 1, "status": "AUTHORIZED_SCOPE_ONLY",
        "grant_id": "grant-1", "account_id": "github:r9327",
        "repository": REPO, "operation": operation, "branch": BRANCH,
        "candidate_sha": SHA, "grant_sha256": "b" * 64,
        "policy_sha": "c" * 40, "decision_epoch": NOW - 10,
        "expires_at": NOW + 3600,
    }
    raw = json.dumps(value, sort_keys=True).encode()
    return raw, hashlib.sha256(raw).hexdigest()


def prepare(operation, **overrides):
    raw, digest = authorization(operation)
    values = dict(
        expected_receipt_sha256=digest, repository=REPO, operation=operation,
        branch=BRANCH, candidate_sha=SHA, now_epoch=NOW,
        expected_account_id="github:r9327", expected_policy_sha="c" * 40,
    )
    if operation == "github.push":
        values["push_state"] = push_state()
    values.update(overrides)
    return prepare_github_mutation(raw, **values)


def pr_state(*, draft):
    return {"number": 42, "draft": draft, "state": "open", "head_sha": SHA,
            "head_branch": BRANCH, "complete": True}


def push_state(**overrides):
    values = dict(
        repository=REPO, branch=BRANCH, candidate_sha=SHA, local_head=SHA,
        remote_main_head="d" * 40, remote_branch_head="e" * 40,
        candidate_contains_main=True, remote_branch_is_ancestor=True,
        observed_at=NOW - 10,
    )
    values.update(overrides)
    return GitPushState(**values)


class GitHubMutationGateTests(unittest.TestCase):
    def test_push_intent_executes_and_certifies_nothing(self):
        intent = prepare("github.push")
        self.assertEqual(intent.operation, "github.push")
        self.assertEqual(intent.push_disposition, PushDisposition.FAST_FORWARD)
        self.assertEqual(intent.remote_main_head, "d" * 40)
        self.assertFalse(intent.executes_action)
        self.assertFalse(intent.certifies_quality)

    def test_push_without_topology_is_rejected_by_default(self):
        with self.assertRaisesRegex(GitHubEvidenceError, "topology"):
            prepare("github.push", push_state=None)

    def test_push_requires_fresh_fetched_main_and_exact_local_head(self):
        for state in (
            push_state(observed_at=NOW - 61),
            push_state(local_head="f" * 40),
            push_state(candidate_contains_main=False),
            push_state(repository="other/repo"),
        ):
            with self.subTest(state=state), self.assertRaises(GitHubEvidenceError):
                prepare("github.push", push_state=state)

    def test_push_never_rewrites_a_diverged_remote_branch(self):
        with self.assertRaisesRegex(GitHubEvidenceError, "diverged"):
            prepare("github.push", push_state=push_state(
                remote_branch_is_ancestor=False))

    def test_new_and_already_published_branches_are_idempotent(self):
        new = prepare("github.push", push_state=push_state(
            remote_branch_head=None, remote_branch_is_ancestor=None))
        current = prepare("github.push", push_state=push_state(
            remote_branch_head=SHA))
        self.assertEqual(new.push_disposition, PushDisposition.NEW_BRANCH)
        self.assertEqual(
            current.push_disposition, PushDisposition.ALREADY_UP_TO_DATE)

    def test_contradictory_new_branch_topology_is_rejected(self):
        with self.assertRaisesRegex(GitHubEvidenceError, "contradictory"):
            prepare("github.push", push_state=push_state(
                remote_branch_head=None, remote_branch_is_ancestor=False))

    def test_authorization_integrity_and_scope_are_exact(self):
        raw, _ = authorization("github.push")
        with self.assertRaisesRegex(GitHubEvidenceError, "integrity"):
            prepare_github_mutation(
                raw, expected_receipt_sha256="0" * 64, repository=REPO,
                operation="github.push", branch=BRANCH, candidate_sha=SHA,
                now_epoch=NOW, expected_account_id="github:r9327",
                expected_policy_sha="c" * 40,
            )
        with self.assertRaisesRegex(GitHubEvidenceError, "scope"):
            prepare("github.push", candidate_sha="d" * 40)

    def test_authorization_must_be_fresh_and_unexpired(self):
        raw, digest = authorization("github.push")
        for now in (NOW + 301, NOW + 3600):
            with self.subTest(now=now), self.assertRaisesRegex(
                    GitHubEvidenceError, "stale or expired"):
                prepare_github_mutation(
                    raw, expected_receipt_sha256=digest, repository=REPO,
                    operation="github.push", branch=BRANCH, candidate_sha=SHA,
                    now_epoch=now, expected_account_id="github:r9327",
                    expected_policy_sha="c" * 40,
                )

    def test_account_and_policy_are_bound_to_supervisor_expectation(self):
        with self.assertRaisesRegex(GitHubEvidenceError, "scope"):
            prepare("github.push", expected_account_id="github:other")
        with self.assertRaisesRegex(GitHubEvidenceError, "provenance"):
            prepare("github.push", expected_policy_sha="d" * 40)

    def test_duplicate_authorization_keys_are_rejected(self):
        raw = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(GitHubEvidenceError, "duplicate JSON"):
            prepare_github_mutation(
                raw, expected_receipt_sha256=hashlib.sha256(raw).hexdigest(),
                repository=REPO, operation="github.push", branch=BRANCH,
                candidate_sha=SHA, now_epoch=NOW,
                expected_account_id="github:r9327", expected_policy_sha="c" * 40,
            )

    def test_main_and_ref_branches_are_refused(self):
        for branch in ("main", "master", "refs/heads/feature", "holding/../main"):
            with self.subTest(branch=branch), self.assertRaisesRegex(
                    GitHubEvidenceError, "trusted GitHub"):
                prepare("github.push", branch=branch)

    def test_ready_requires_complete_current_draft_at_exact_head(self):
        intent = prepare("github.pr_ready", pr_state=pr_state(draft=True))
        self.assertEqual(intent.pr_number, 42)
        for field, value in (("complete", False), ("head_sha", "d" * 40),
                             ("state", "closed"), ("draft", False)):
            current = pr_state(draft=True); current[field] = value
            with self.subTest(field=field), self.assertRaises(GitHubEvidenceError):
                prepare("github.pr_ready", pr_state=current)

    def test_draft_requires_complete_current_non_draft_at_exact_head(self):
        intent = prepare("github.pr_draft", pr_state=pr_state(draft=False))
        self.assertEqual(intent.pr_number, 42)
        with self.assertRaisesRegex(GitHubEvidenceError, "draft state"):
            prepare("github.pr_draft", pr_state=pr_state(draft=True))

    def test_ci_trigger_requires_complete_non_draft_current_head(self):
        intent = prepare("github.ci_trigger", pr_state=pr_state(draft=False))
        self.assertEqual(intent.pr_number, 42)
        with self.assertRaisesRegex(GitHubEvidenceError, "draft state"):
            prepare("github.ci_trigger", pr_state=pr_state(draft=True))

    def test_merge_requires_full_evidence_for_same_repo_and_sha(self):
        full = FullEvidence(REPO, 42, SHA, 77, 88)
        intent = prepare(
            "github.merge", full_evidence=full, pr_state=pr_state(draft=False))
        self.assertEqual((intent.pr_number, intent.evidence_run_id), (42, 77))
        with self.assertRaisesRegex(GitHubEvidenceError, "FULL"):
            prepare("github.merge")
        with self.assertRaisesRegex(GitHubEvidenceError, "FULL"):
            prepare(
                "github.merge",
                full_evidence=FullEvidence(REPO, 42, "d" * 40, 77, 88),
                pr_state=pr_state(draft=False),
            )
        wrong_branch = pr_state(draft=False)
        wrong_branch["head_branch"] = "holding/other"
        with self.assertRaisesRegex(GitHubEvidenceError, "FULL"):
            prepare("github.merge", full_evidence=full, pr_state=wrong_branch)

    def test_unexpected_evidence_is_rejected(self):
        with self.assertRaisesRegex(GitHubEvidenceError, "Unexpected"):
            prepare("github.push", pr_state=pr_state(draft=True))


if __name__ == "__main__":
    unittest.main()

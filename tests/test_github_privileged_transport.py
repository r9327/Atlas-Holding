import io
import json
import unittest
from urllib import error

from holding.github_full_evidence import FullEvidence, GitHubEvidenceError
from holding.github_mutation_gate import GitHubMutationIntent
from holding.github_privileged_transport import (
    GitHubCredentialRejected, GitHubPrivilegedTransport,
)
from holding.sync_coordinator import MutationFailure


REPO = "r9327/Dof-Atlas"
BRANCH = "holding/lot-1"
SHA = "a" * 40
KEY = "b" * 64
TOKEN = "private-test-token"


class Reply:
    def __init__(self, value=None, *, status=200):
        self.status = status
        self.raw = b"" if value is None else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit):
        return self.raw[:limit]


class FakeOpener:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def pull(*, draft=True, sha=SHA, state="open", merged=False):
    return {
        "number": 42, "state": state, "draft": draft, "merged": merged,
        "node_id": "PR_node_42",
        "head": {"ref": BRANCH, "sha": sha, "repo": {"full_name": REPO}},
    }


def runs(run_id=10):
    rows = [] if run_id is None else [{"id": run_id, "head_sha": SHA}]
    return {"workflow_runs": rows}


def intent(operation, *, evidence_run_id=None):
    return GitHubMutationIntent(
        operation, REPO, BRANCH, SHA, 42, "c" * 64, evidence_run_id,
    )


class GitHubPrivilegedTransportTests(unittest.TestCase):
    def transport(self, opener, evidence=None):
        return GitHubPrivilegedTransport(
            REPO, TOKEN, opener=opener, evidence_client=evidence,
        )

    def test_observe_pr_binds_repo_branch_sha_and_latest_dispatch(self):
        opener = FakeOpener(Reply(pull()), Reply(runs(10)))
        state = self.transport(opener).observe_pr(REPO, 42)
        self.assertEqual((state.repository, state.head_branch, state.head_sha),
                         (REPO, BRANCH, SHA))
        self.assertEqual((state.ci_run_id, state.ci_head_sha), (10, SHA))
        self.assertEqual(
            opener.requests[0].get_header("X-github-api-version"),
            "2026-03-10",
        )
        self.assertNotIn(TOKEN, repr(state))

    def test_account_probe_uses_fixed_user_endpoint_and_returns_no_token(self):
        opener = FakeOpener(Reply({"login": "r9327", "private": "ignored"}))
        account = self.transport(opener).observe_account()
        self.assertEqual(account, "github:r9327")
        self.assertEqual(opener.requests[0].full_url, "https://api.github.com/user")
        self.assertEqual(
            opener.requests[0].get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, account)

    def test_account_probe_distinguishes_explicit_credential_rejection(self):
        denied = error.HTTPError(
            "https://api.github.com/user", 401, "private detail", {}, None)
        with self.assertRaises(GitHubCredentialRejected) as raised:
            self.transport(FakeOpener(denied)).observe_account()
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_account_probe_does_not_treat_rate_limit_as_bad_credentials(self):
        limited = error.HTTPError(
            "https://api.github.com/user", 403, "private detail",
            {"X-RateLimit-Remaining": "0"}, None)
        with self.assertRaises(GitHubEvidenceError) as raised:
            self.transport(FakeOpener(limited)).observe_account()
        self.assertNotIsInstance(raised.exception, GitHubCredentialRejected)

    def test_cross_repository_pull_is_rejected(self):
        bad = pull()
        bad["head"]["repo"]["full_name"] = "attacker/fork"
        opener = FakeOpener(Reply(bad), Reply(runs()))
        with self.assertRaises(GitHubEvidenceError):
            self.transport(opener).observe_pr(REPO, 42)

    def test_ready_rechecks_exact_head_then_uses_graphql(self):
        response = {"data": {"markPullRequestReadyForReview": {
            "clientMutationId": KEY,
            "pullRequest": {"id": "PR_node_42", "isDraft": False},
        }}}
        opener = FakeOpener(Reply(pull()), Reply(response))
        self.transport(opener).mutate(intent("github.pr_ready"), KEY)
        graphql = opener.requests[-1]
        self.assertEqual(graphql.full_url, "https://api.github.com/graphql")
        body = json.loads(graphql.data)
        self.assertEqual(body["query"], (
            "mutation($id:ID!,$clientMutationId:String!){"
            "markPullRequestReadyForReview(input:{pullRequestId:$id,"
            "clientMutationId:$clientMutationId}){clientMutationId "
            "pullRequest{id isDraft}}}"
        ))
        self.assertEqual(body["variables"]["clientMutationId"], KEY)

    def test_moved_head_is_known_rejection_before_mutation(self):
        opener = FakeOpener(Reply(pull(sha="d" * 40)))
        with self.assertRaises(MutationFailure) as raised:
            self.transport(opener).mutate(intent("github.pr_ready"), KEY)
        self.assertTrue(raised.exception.outcome_known)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(len(opener.requests), 1)

    def test_preflight_network_failure_is_known_to_have_not_mutated(self):
        opener = FakeOpener(error.URLError("private-network-detail"))
        with self.assertRaises(MutationFailure) as raised:
            self.transport(opener).mutate(intent("github.pr_ready"), KEY)
        self.assertTrue(raised.exception.outcome_known)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("private-network-detail", str(raised.exception))

    def test_malformed_intent_is_rejected_without_network(self):
        opener = FakeOpener()
        malformed = GitHubMutationIntent(
            "github.pr_ready", REPO, "main", SHA, 42, "c" * 64, None)
        with self.assertRaises(MutationFailure) as raised:
            self.transport(opener).mutate(malformed, KEY)
        self.assertTrue(raised.exception.outcome_known)
        self.assertEqual(opener.requests, [])

    def test_ci_dispatch_uses_only_fixed_workflow_and_authorized_branch(self):
        opener = FakeOpener(Reply(pull(draft=False)), Reply(None, status=204))
        self.transport(opener).mutate(intent("github.ci_trigger"), KEY)
        dispatched = opener.requests[-1]
        self.assertTrue(dispatched.full_url.endswith(
            "/actions/workflows/phase-certification.yml/dispatches"))
        self.assertEqual(json.loads(dispatched.data), {"ref": BRANCH})

    def test_merge_revalidates_full_and_sends_atomic_sha(self):
        evidence = object()
        opener = FakeOpener(Reply(pull(draft=False)), Reply({"merged": True}))
        transport = self.transport(opener, evidence=evidence)
        original = transport.observe_full
        transport.observe_full = lambda repository, number: FullEvidence(
            REPO, 42, SHA, 77, 88)
        self.addCleanup(setattr, transport, "observe_full", original)
        transport.mutate(intent("github.merge", evidence_run_id=77), KEY)
        merged = opener.requests[-1]
        self.assertEqual(merged.get_method(), "PUT")
        self.assertEqual(json.loads(merged.data), {
            "merge_method": "squash", "sha": SHA,
        })

    def test_server_failure_is_ambiguous_and_never_leaks_token(self):
        failure = error.HTTPError(
            "https://api.github.com/graphql", 502, "secret upstream", {}, io.BytesIO())
        opener = FakeOpener(Reply(pull()), failure)
        with self.assertRaises(MutationFailure) as raised:
            self.transport(opener).mutate(intent("github.pr_ready"), KEY)
        self.assertFalse(raised.exception.outcome_known)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_graphql_mismatched_pull_result_is_ambiguous(self):
        response = {"data": {"markPullRequestReadyForReview": {
            "clientMutationId": KEY,
            "pullRequest": {"id": "another-node", "isDraft": False},
        }}}
        opener = FakeOpener(Reply(pull()), Reply(response))
        with self.assertRaises(MutationFailure) as raised:
            self.transport(opener).mutate(intent("github.pr_ready"), KEY)
        self.assertFalse(raised.exception.outcome_known)

    def test_rate_limit_is_known_retryable_failure(self):
        failure = error.HTTPError(
            "https://api.github.com/graphql", 403, "forbidden",
            {"X-RateLimit-Remaining": "0"}, io.BytesIO())
        opener = FakeOpener(Reply(pull()), failure)
        with self.assertRaises(MutationFailure) as raised:
            self.transport(opener).mutate(intent("github.pr_ready"), KEY)
        self.assertEqual(raised.exception.code, "RATE_LIMITED")
        self.assertTrue(raised.exception.outcome_known)

    def test_push_is_not_a_supported_network_mutation(self):
        opener = FakeOpener()
        with self.assertRaises(MutationFailure):
            self.transport(opener).mutate(
                GitHubMutationIntent(
                    "github.push", REPO, BRANCH, SHA, None, "c" * 64, None),
                KEY,
            )
        self.assertEqual(opener.requests, [])


if __name__ == "__main__":
    unittest.main()

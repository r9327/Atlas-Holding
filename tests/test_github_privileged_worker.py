import hashlib
import json
import unittest

from holding.github_broker_protocol import SCHEMA_VERSION
from holding.github_full_evidence import FullEvidence
from holding.git_push import GitPushState
from holding.github_mutation_gate import GitHubMutationIntent
from holding.github_privileged_worker import handle_request
from holding.github_privileged_transport import GitHubCredentialRejected
from holding.sync_coordinator import LiveGitHubState, MutationFailure


REPO = "r9327/Dof-Atlas"
BRANCH = "holding/lot-1"
SHA = "a" * 40
KEY = "b" * 64


def request(action, payload):
    base = {"schema_version": SCHEMA_VERSION, "action": action, "payload": payload}
    canonical = json.dumps(base, sort_keys=True, separators=(",", ":")).encode()
    value = {**base, "request_id": hashlib.sha256(canonical).hexdigest()}
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class FakeTransport:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def observe_pr(self, repository, pr_number):
        self.calls.append(("observe_pr", repository, pr_number))
        if self.failure:
            raise self.failure
        return LiveGitHubState(
            REPO, 42, BRANCH, SHA, "open", True, True,
            ci_run_id=10, ci_head_sha=SHA)

    def observe_account(self):
        self.calls.append(("observe_account",))
        if self.failure:
            raise self.failure
        return "github:r9327"

    def observe_full(self, repository, pr_number):
        self.calls.append(("observe_full", repository, pr_number))
        return FullEvidence(REPO, 42, SHA, 77, 88)

    def mutate(self, intent, idempotency_key):
        self.calls.append(("mutate", intent, idempotency_key))
        if self.failure:
            raise self.failure


class FakeGitControl:
    def __init__(self):
        self.calls = []

    def observe(self, repository, branch, candidate_sha):
        self.calls.append(("observe", repository, branch, candidate_sha))
        return GitPushState(repository, branch, candidate_sha, None, True)

    def push(self, intent, idempotency_key):
        self.calls.append(("push", intent, idempotency_key))


def push_intent():
    return {
        "operation": "github.push", "repository": REPO,
        "branch": BRANCH, "candidate_sha": SHA, "pr_number": None,
        "authorization_sha256": "c" * 64, "evidence_run_id": None,
        "executes_action": True, "certifies_quality": False,
    }


class PrivilegedWorkerTests(unittest.TestCase):
    def test_account_probe_returns_only_stable_identity(self):
        transport = FakeTransport()
        response = json.loads(handle_request(
            request("observe_account", {}), transport))
        self.assertEqual(response["result"], {"account_id": "github:r9327"})
        self.assertEqual(transport.calls, [("observe_account",)])

    def test_account_probe_credential_rejection_is_stable_and_nonretryable(self):
        transport = FakeTransport(GitHubCredentialRejected("private-token-detail"))
        response = json.loads(handle_request(
            request("observe_account", {}), transport))
        self.assertEqual(response["status"], "ERROR")
        self.assertEqual(response["code"], "REJECTED")
        self.assertFalse(response["retryable"])
        self.assertNotIn("private-token-detail", json.dumps(response))

    def test_observation_roundtrip_is_canonical_and_secret_free(self):
        transport = FakeTransport()
        raw = request("observe_pr", {"repository": REPO, "pr_number": 42})
        response = json.loads(handle_request(raw, transport))
        self.assertEqual(response["status"], "OK")
        self.assertEqual(response["result"]["head_sha"], SHA)
        self.assertEqual(transport.calls, [("observe_pr", REPO, 42)])

    def test_mutation_reconstructs_exact_immutable_intent(self):
        transport = FakeTransport()
        intent = GitHubMutationIntent(
            "github.pr_ready", REPO, BRANCH, SHA, 42, "c" * 64, None)
        raw = request("mutate", {
            "intent": {
                "operation": intent.operation, "repository": intent.repository,
                "branch": intent.branch, "candidate_sha": intent.candidate_sha,
                "pr_number": intent.pr_number,
                "authorization_sha256": intent.authorization_sha256,
                "evidence_run_id": None, "executes_action": False,
                "certifies_quality": False,
            }, "idempotency_key": KEY,
        })
        response = json.loads(handle_request(raw, transport))
        self.assertEqual(response["result"], {})
        self.assertEqual(transport.calls[0][1], intent)

    def test_mutation_failure_keeps_only_stable_fields(self):
        transport = FakeTransport(MutationFailure(
            "TRANSIENT_UNAVAILABLE", retryable=True, outcome_known=False))
        intent = {
            "operation": "github.pr_ready", "repository": REPO,
            "branch": BRANCH, "candidate_sha": SHA, "pr_number": 42,
            "authorization_sha256": "c" * 64, "evidence_run_id": None,
            "executes_action": False, "certifies_quality": False,
        }
        response = json.loads(handle_request(request(
            "mutate", {"intent": intent, "idempotency_key": KEY}), transport))
        self.assertEqual(response["status"], "ERROR")
        self.assertEqual(set(response), {
            "schema_version", "request_id", "status", "code",
            "retryable", "outcome_known"})

    def test_git_observe_and_push_use_exact_source_inside_broker(self):
        transport = FakeTransport()
        git = FakeGitControl()
        factory_calls = []
        def factory(source_id):
            factory_calls.append(source_id)
            return git
        observed = json.loads(handle_request(request("observe_git", {
            "source_id": "source-1", "repository": REPO,
            "branch": BRANCH, "candidate_sha": SHA,
        }), transport, git_control_factory=factory))
        pushed = json.loads(handle_request(request("push_git", {
            "source_id": "source-1", "intent": push_intent(),
            "idempotency_key": KEY,
        }), transport, git_control_factory=factory))
        self.assertEqual(observed["result"]["candidate_sha"], SHA)
        self.assertEqual(pushed["result"], {})
        self.assertEqual(factory_calls, ["source-1", "source-1"])
        self.assertEqual(git.calls[0], ("observe", REPO, BRANCH, SHA))
        self.assertEqual(git.calls[1][0], "push")

    def test_git_push_without_privileged_git_binding_is_rejected(self):
        response = json.loads(handle_request(request("push_git", {
            "source_id": "source-1", "intent": push_intent(),
            "idempotency_key": KEY,
        }), FakeTransport()))
        self.assertEqual(response["code"], "REJECTED")
        self.assertFalse(response["retryable"])
        self.assertTrue(response["outcome_known"])

    def test_unsafe_git_source_never_reaches_privileged_factory(self):
        calls = []
        response = json.loads(handle_request(request("observe_git", {
            "source_id": "../canonical", "repository": REPO,
            "branch": BRANCH, "candidate_sha": SHA,
        }), FakeTransport(), git_control_factory=lambda source: calls.append(source)))
        self.assertEqual(response["status"], "ERROR")
        self.assertEqual(calls, [])

    def test_tampered_request_id_never_executes(self):
        transport = FakeTransport()
        value = json.loads(request(
            "observe_pr", {"repository": REPO, "pr_number": 42}))
        value["request_id"] = "0" * 64
        response = json.loads(handle_request(json.dumps(value).encode(), transport))
        self.assertEqual(response["status"], "ERROR")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()

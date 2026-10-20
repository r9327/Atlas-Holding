import hashlib
import json
import unittest

from holding.standing_grant import (
    StandingGrantError,
    authorize_operation,
    serialize_github_authorization,
)


POLICY_SHA = "a" * 40
CANDIDATE_SHA = "b" * 40
NOW = 1_800_000_000


def grant():
    return {
        "schema_version": 1,
        "grant_id": "grant-1",
        "account_id": "github:r9327",
        "repository": "r9327/Dof-Atlas",
        "operations": [
            "repo.read", "roadmap.plan", "repo.edit", "commit.create",
            "github.push", "github.pr_draft", "github.pr_ready",
            "github.ci_trigger", "github.merge",
        ],
        "issued_at": NOW - 100,
        "expires_at": NOW + 10_000,
        "revoked_at": None,
        "policy_sha": POLICY_SHA,
    }


def authorize(payload, operation="github.push", **overrides):
    raw = json.dumps(payload, sort_keys=True).encode()
    arguments = {
        "expected_record_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_policy_sha": POLICY_SHA,
        "observed_account_id": "github:r9327",
        "repository": "r9327/Dof-Atlas",
        "operation": operation,
        "now_epoch": NOW,
        "branch": "codex/lot-1" if operation not in {"repo.read", "roadmap.plan"} else None,
        "candidate_sha": CANDIDATE_SHA if operation.startswith("github.") else None,
    }
    arguments.update(overrides)
    return authorize_operation(raw, **arguments)


class StandingGrantTests(unittest.TestCase):
    def test_valid_grant_authorizes_scope_but_executes_and_certifies_nothing(self):
        decision = authorize(grant())
        self.assertEqual(decision.status, "AUTHORIZED_SCOPE_ONLY")
        self.assertFalse(decision.executes_action)
        self.assertFalse(decision.certifies_quality)

    def test_github_decision_serializes_to_canonical_consumer_contract(self):
        decision = authorize(grant())
        first = serialize_github_authorization(decision)
        second = serialize_github_authorization(decision)
        self.assertEqual(first, second)
        self.assertEqual(first.decode("ascii"), json.dumps({
            "schema_version": 1,
            "status": "AUTHORIZED_SCOPE_ONLY",
            "grant_id": "grant-1",
            "account_id": "github:r9327",
            "repository": "r9327/Dof-Atlas",
            "operation": "github.push",
            "branch": "codex/lot-1",
            "candidate_sha": CANDIDATE_SHA,
            "grant_sha256": decision.grant_sha256,
            "policy_sha": POLICY_SHA,
            "decision_epoch": NOW,
            "expires_at": NOW + 10_000,
        }, sort_keys=True, separators=(",", ":")))

    def test_non_github_decision_cannot_be_serialized_as_github_authority(self):
        decision = authorize(
            grant(), operation="repo.read", branch=None, candidate_sha=None)
        with self.assertRaisesRegex(StandingGrantError, "GitHub decision"):
            serialize_github_authorization(decision)

    def test_record_digest_is_bound_to_trusted_storage(self):
        payload = grant()
        raw = json.dumps(payload, sort_keys=True).encode()
        with self.assertRaisesRegex(StandingGrantError, "integrity"):
            authorize_operation(
                raw, expected_record_sha256="0" * 64,
                expected_policy_sha=POLICY_SHA, observed_account_id="github:r9327",
                repository="r9327/Dof-Atlas", operation="repo.read", now_epoch=NOW,
            )

    def test_account_repository_and_policy_are_exact(self):
        cases = (
            {"observed_account_id": "github:other"},
            {"repository": "r9327/Other"},
            {"expected_policy_sha": "0" * 40},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(StandingGrantError):
                authorize(grant(), **values)

    def test_operation_must_be_explicitly_granted(self):
        payload = grant(); payload["operations"].remove("github.merge")
        with self.assertRaisesRegex(StandingGrantError, "outside"):
            authorize(payload, operation="github.merge")

    def test_forbidden_operation_is_rejected_before_record(self):
        with self.assertRaisesRegex(StandingGrantError, "trusted authorization"):
            authorize(grant(), operation="github.force_push")

    def test_expired_future_or_overlong_grant_is_rejected(self):
        payload = grant(); payload["expires_at"] = NOW
        with self.assertRaisesRegex(StandingGrantError, "currently valid"):
            authorize(payload)
        payload = grant(); payload["issued_at"] = NOW + 1
        with self.assertRaisesRegex(StandingGrantError, "currently valid"):
            authorize(payload)
        payload = grant(); payload["expires_at"] = payload["issued_at"] + 367 * 86400
        with self.assertRaisesRegex(StandingGrantError, "currently valid"):
            authorize(payload)

    def test_revoked_grant_is_rejected(self):
        payload = grant(); payload["revoked_at"] = NOW - 1
        with self.assertRaisesRegex(StandingGrantError, "revoked"):
            authorize(payload)

    def test_main_or_unsafe_branch_is_never_authorized(self):
        for branch in ("main", "master", "refs/heads/main", "codex/../main", "codex//x"):
            with self.subTest(branch=branch), self.assertRaisesRegex(
                    StandingGrantError, "safe dedicated branch"):
                authorize(grant(), branch=branch)

    def test_github_operation_requires_exact_candidate_sha(self):
        for sha in (None, "short", True):
            with self.subTest(sha=sha), self.assertRaisesRegex(
                    StandingGrantError, "exact candidate SHA"):
                authorize(grant(), candidate_sha=sha)

    def test_boolean_timestamps_are_rejected(self):
        payload = grant(); payload["expires_at"] = True
        with self.assertRaisesRegex(StandingGrantError, "expiry"):
            authorize(payload)

    def test_malformed_operations_and_duplicate_json_keys_fail_closed(self):
        payload = grant(); payload["operations"] = [{"operation": "repo.read"}]
        with self.assertRaisesRegex(StandingGrantError, "invalid operations"):
            authorize(payload, operation="repo.read")
        raw = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(StandingGrantError, "duplicate JSON"):
            authorize_operation(
                raw, expected_record_sha256=hashlib.sha256(raw).hexdigest(),
                expected_policy_sha=POLICY_SHA, observed_account_id="github:r9327",
                repository="r9327/Dof-Atlas", operation="repo.read", now_epoch=NOW,
            )


if __name__ == "__main__":
    unittest.main()

"""F10 privileged PR changed-path hints never certify implementation."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import unittest
from unittest.mock import patch

from holding.github_pr_changed_paths import (
    PrChangedPathsError, parse_pr_changed_paths, validate_pr_changed_paths,
)
from holding.github_broker_protocol import GitHubBrokerProcessControl, SCHEMA_VERSION
from holding.github_full_evidence import GitHubEvidenceError
from holding.github_privileged_transport import GitHubPrivilegedTransport
from holding.github_privileged_worker import handle_request

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
TOKEN = "do-not-share-credential"


def changed(path="app/feature.py", status="modified"):
    return {"filename": path, "status": status, "patch": "not returned to model"}


def pull(sha=SHA, repository=REPO):
    return {"number": 12, "state": "open",
            "head": {"sha": sha, "ref": "holding/feature",
                     "repo": {"full_name": REPO}},
            "base": {"repo": {"full_name": repository}}}


class Response:
    status = 200

    def __init__(self, value):
        self.raw = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.raw[:size]


class Opener:
    def __init__(self, *replies):
        self.responses = list(replies)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        return self.responses.pop(0)


def request(action, payload):
    value = {"schema_version": SCHEMA_VERSION, "action": action,
             "payload": payload}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode()
    return json.dumps({**value, "request_id": hashlib.sha256(raw).hexdigest()},
                      sort_keys=True, separators=(",", ":")).encode()


class ChangedPrPathsTests(unittest.TestCase):
    def test_sanitized_bounded_metadata_is_not_a_certification(self):
        report = parse_pr_changed_paths(REPO, 12, SHA, [
            changed(), changed("docs/README.md", "added"),
            changed("secrets/key.pem", "removed")])
        self.assertEqual(report.paths, ("app/feature.py", "docs/README.md"))
        self.assertEqual(report.status, "PR_PATHS_PARTIAL")
        self.assertEqual(report.listed_files, 3)
        self.assertFalse(report.can_certify)
        self.assertFalse(report.can_skip_implementation)
        self.assertEqual(validate_pr_changed_paths(
            REPO, json.loads(json.dumps(asdict(report)))), report)

    def test_first_page_and_excess_files_are_explicitly_partial(self):
        report = parse_pr_changed_paths(REPO, 12, SHA, [
            changed(f"app/module_{i:03}.py") for i in range(100)])
        self.assertEqual(report.status, "PR_PATHS_PARTIAL")
        self.assertEqual(report.listed_files, 100)
        self.assertEqual(len(report.paths), 20)
        self.assertEqual(parse_pr_changed_paths(REPO, 12, SHA, []).status,
                         "PR_PATHS_PARTIAL")
        self.assertEqual(parse_pr_changed_paths(
            REPO, 12, SHA, [changed()]).status, "PR_PATHS_BOUNDED")

    def test_malformed_status_duplicate_and_head_are_rejected(self):
        for rows in ([changed(), changed()], [changed(status=["modified"])],
                     [changed("bad\nfile.py")], [changed(status="unknown")],
                     [changed("test.py", "renamed")] * 2):
            with self.subTest(rows=rows), self.assertRaises(PrChangedPathsError):
                parse_pr_changed_paths(REPO, 12, SHA, rows)
        with self.assertRaises(PrChangedPathsError):
            parse_pr_changed_paths(REPO, 12, "wrong", [changed()])
        legitimate = json.loads(json.dumps(asdict(parse_pr_changed_paths(
            REPO, 12, SHA, [changed()]))))
        for bad in (
                {"can_skip_implementation": True},
                {"head_sha": "x" * 40},
                {"listed_files": 100},
                {"status": ["PR_PATHS_BOUNDED"]},
                {"paths": ["../secret.py"]}):
            with self.subTest(bad=bad), self.assertRaises(PrChangedPathsError):
                validate_pr_changed_paths(REPO, {**legitimate, **bad})

    def test_transport_observes_same_head_before_and_after_paths(self):
        opener = Opener(Response(pull()), Response([changed()]), Response(pull()))
        transport = GitHubPrivilegedTransport(REPO, TOKEN, opener=opener)
        report = transport.observe_pr_paths(REPO, 12, SHA)
        self.assertEqual(report.paths, ("app/feature.py",))
        self.assertEqual([x.get_method() for x in opener.requests],
                         ["GET", "GET", "GET"])
        self.assertIn("pulls/12/files?per_page=100",
                      opener.requests[1].full_url)
        self.assertNotIn(TOKEN, repr(report))
        self.assertTrue(all(x.get_header("Authorization") == "Bearer " + TOKEN
                            for x in opener.requests))

    def test_moved_head_cross_repo_or_invalid_request_never_returns_paths(self):
        for replies in (
            (Response(pull("b" * 40)),),
            (Response(pull(repository="other/repo")),),
            (Response(pull()), Response([changed()]), Response(pull("c" * 40))),
        ):
            with self.subTest(replies=len(replies)):
                transport = GitHubPrivilegedTransport(
                    REPO, TOKEN, opener=Opener(*replies))
                with self.assertRaises(GitHubEvidenceError):
                    transport.observe_pr_paths(REPO, 12, SHA)
        transport = GitHubPrivilegedTransport(
            REPO, TOKEN, opener=Opener())
        with self.assertRaises(GitHubEvidenceError):
            transport.observe_pr_paths("other/repo", 12, SHA)

    def test_ipc_observes_fixed_head_and_unprivileged_caller_revalidates(self):
        transport = GitHubPrivilegedTransport(REPO, TOKEN, opener=Opener(
            Response(pull()), Response([changed()]), Response(pull())))
        raw = handle_request(request("observe_pr_paths", {
            "repository": REPO, "pr_number": 12, "head_sha": SHA,
        }), transport)
        reply = json.loads(raw)
        self.assertEqual(reply["status"], "OK")
        self.assertNotIn(TOKEN, raw.decode())
        broker = object.__new__(GitHubBrokerProcessControl)
        with patch.object(broker, "_invoke", return_value=reply["result"]) as call:
            report = broker.observe_pr_paths(REPO, 12, SHA)
        self.assertEqual(report.pr_number, 12)
        call.assert_called_once()
        with patch.object(broker, "_invoke", return_value={
                **reply["result"], "head_sha": "b" * 40}):
            with self.assertRaises(GitHubEvidenceError):
                broker.observe_pr_paths(REPO, 12, SHA)


if __name__ == "__main__":
    unittest.main()

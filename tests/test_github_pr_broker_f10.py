"""Actual F10 read-only PR observation across transport, IPC and caller."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from holding.github_broker_protocol import (
    GitHubBrokerProcessControl, SCHEMA_VERSION,
)
from holding.github_full_evidence import GitHubEvidenceError
from holding.github_pr_inventory import parse_open_prs
from holding.github_privileged_transport import GitHubPrivilegedTransport
from holding.github_privileged_worker import handle_request

REPO = "r9327/Dof-Atlas"
TOKEN = "never-leak-this-credential"


class Response:
    status = 200

    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.raw[:size]


class Opener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return self.response


def pr():
    return {"number": 14, "state": "open", "draft": True,
            "head": {"sha": "a" * 40, "ref": "holding/feature",
                     "repo": {"full_name": REPO}},
            "base": {"sha": "b" * 40, "repo": {"full_name": REPO}}}


def message(action, payload):
    base = {"schema_version": SCHEMA_VERSION, "action": action,
            "payload": payload}
    raw = json.dumps(base, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("ascii")
    request_id = hashlib.sha256(raw).hexdigest()
    return json.dumps({**base, "request_id": request_id},
                      separators=(",", ":"), sort_keys=True).encode()


class GitHubPrBrokerTests(unittest.TestCase):
    def test_existing_transport_uses_authenticated_read_only_endpoint(self):
        opener = Opener(Response([pr()]))
        transport = GitHubPrivilegedTransport(REPO, TOKEN, opener=opener)
        result = transport.observe_open_prs(REPO)
        self.assertEqual(result.observed_count, 1)
        self.assertTrue(result.hints[0].draft)
        self.assertFalse(result.can_certify)
        self.assertEqual(len(opener.requests), 1)
        request = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("pulls?state=open&per_page=100",
                      request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertNotIn(TOKEN, repr(result))

    def test_cross_repository_refused_before_network(self):
        opener = Opener(Response([pr()]))
        transport = GitHubPrivilegedTransport(REPO, TOKEN, opener=opener)
        with self.assertRaises(GitHubEvidenceError):
            transport.observe_open_prs("other/repo")
        self.assertEqual(opener.requests, [])

    def test_privileged_worker_keeps_secret_out_of_ipc(self):
        transport = GitHubPrivilegedTransport(
            REPO, TOKEN, opener=Opener(Response([pr()])))
        result = json.loads(handle_request(
            message("observe_open_prs", {"repository": REPO}), transport))
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["result"]["hints"][0]["number"], 14)
        self.assertNotIn(TOKEN, json.dumps(result))
        forged = json.loads(handle_request(
            message("observe_open_prs", {"repository": "other/repo"}), transport))
        self.assertEqual(forged["status"], "ERROR")

    def test_unprivileged_caller_revalidates_every_field(self):
        good = json.loads(json.dumps(asdict(parse_open_prs(REPO, [pr()]))))
        observer = object.__new__(GitHubBrokerProcessControl)
        with patch.object(observer, "_invoke", return_value=good) as invoke:
            report = observer.observe_open_prs(REPO)
        self.assertEqual(report.hints[0].head_sha, "a" * 40)
        invoke.assert_called_once_with(
            "observe_open_prs", {"repository": REPO}, mutation=False)
        for forged in (
                {**good, "can_skip_implementation": True},
                {**good, "repository": "another/repo"},
                {**good, "hints": [{**good["hints"][0], "number": True}]}):
            with patch.object(observer, "_invoke", return_value=forged):
                with self.assertRaises(GitHubEvidenceError):
                    observer.observe_open_prs(REPO)


if __name__ == "__main__":
    unittest.main()

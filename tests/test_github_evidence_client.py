"""GitHub API client refuses redirects, oversized data and unsafe routes."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from holding.github_full_evidence import (
    GitHubEvidenceClient, GitHubEvidenceError, _NoRedirect,
)


class _Response:
    status = 200

    def __init__(self, raw: bytes):
        self.raw = raw
        self.requested = None

    def __enter__(self):
        return self

    def __exit__(self, *ignored):
        return None

    def read(self, length: int):
        self.requested = length
        return self.raw[:length]


class _Opener:
    def __init__(self, response):
        self.response = response
        self.urls = []
        self.timeouts = []
        self.authorization = []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        self.timeouts.append(timeout)
        self.authorization.append(request.get_header("Authorization"))
        return self.response


class ClientSafetyTests(unittest.TestCase):
    def test_api_get_has_fixed_hostname_https_and_no_proxy(self):
        response = _Response(b'{"good":true}')
        opener = _Opener(response)
        with patch("holding.github_full_evidence.request.build_opener",
                   return_value=opener) as builder:
            client = GitHubEvidenceClient("r9327/Dof-Atlas", "secret-fixture")
            self.assertEqual(client.get("pulls/42"), {"good": True})
        self.assertEqual(opener.urls, ["https://api.github.com/repos/r9327/Dof-Atlas/pulls/42"])
        self.assertEqual(opener.timeouts, [15])
        self.assertEqual(opener.authorization, ["Bearer secret-fixture"])
        self.assertTrue(any(isinstance(arg, _NoRedirect) for arg in builder.call_args.args))
        self.assertEqual(response.requested, 2 * 1024 * 1024 + 1)

    def test_invalid_route_rejected_before_any_request(self):
        opener = _Opener(_Response(b"{}"))
        with patch("holding.github_full_evidence.request.build_opener",
                   return_value=opener):
            client = GitHubEvidenceClient("r9327/Dof-Atlas", "secret")
            for route in ("", "../secrets", "/users/x", "https://evil.test/x",
                          "pulls/../actions", "pulls\\1", "pulls/%2e%2e/actions",
                          "pulls/1#fragment", "pulls/1 bad"):
                with self.subTest(route=route), self.assertRaises(GitHubEvidenceError):
                    client.get(route)
        self.assertEqual(opener.urls, [])

    def test_repository_path_segments_cannot_traverse(self):
        for repo in ("../actions", "owner/..", "./repo", "owner/."):
            with self.subTest(repo=repo), self.assertRaises(GitHubEvidenceError):
                GitHubEvidenceClient(repo, "secret")

    def test_oversized_payload_rejected_without_unbounded_read(self):
        response = _Response(b"x" * (2 * 1024 * 1024 + 1))
        with patch("holding.github_full_evidence.request.build_opener",
                   return_value=_Opener(response)):
            client = GitHubEvidenceClient("r9327/Dof-Atlas", "secret")
            with self.assertRaisesRegex(GitHubEvidenceError, "2 MiB"):
                client.get("actions/runs")
        self.assertEqual(response.requested, 2 * 1024 * 1024 + 1)

    def test_unstructured_json_and_no_authorization_are_rejected(self):
        opener = _Opener(_Response(b"[]"))
        with patch("holding.github_full_evidence.request.build_opener",
                   return_value=opener):
            client = GitHubEvidenceClient("r9327/Dof-Atlas", "secret")
            with self.assertRaises(GitHubEvidenceError):
                client.get("pulls/3")
        with self.assertRaises(GitHubEvidenceError):
            GitHubEvidenceClient("r9327/Dof-Atlas", "")

    def test_redirect_to_other_host_is_never_followed(self):
        handler = _NoRedirect()
        self.assertIsNone(handler.redirect_request(
            None, None, 302, "Found", {},
            "https://attacker.invalid/steal-bearer"))


if __name__ == "__main__":
    unittest.main()

"""Verify safe external GitHub evidence CLI cannot leak tokens or fake a PASS."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.github_evidence_cli import main, read_token_file
from holding.github_full_evidence import FullEvidence, GitHubEvidenceError

SHA = "a" * 40
REPO = "r9327/Dof-Atlas"


class EvidenceCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = self.root / "github.token"
        self.token.write_text("test-private-github-token", encoding="utf-8")
        self.token.chmod(0o600)
        self.args = ["--repo", REPO, "--pr", "230",
                     "--candidate-sha", SHA,
                     "--token-file", str(self.token)]

    def test_read_only_cli_returns_provenance_without_token(self):
        evidence = FullEvidence(REPO, 230, SHA, 77, 88)
        output = StringIO()
        with patch("holding.github_evidence_cli.GitHubEvidenceClient") as client, \
             patch("holding.github_evidence_cli.observe_full_pr",
                   return_value=evidence) as verify:
            with redirect_stdout(output):
                self.assertEqual(main(self.args), 0)
            client.assert_called_once_with(REPO, "test-private-github-token")
            verify.assert_called_once_with(client.return_value, 230)
        self.assertEqual(json.loads(output.getvalue())["run_id"], 77)
        self.assertNotIn("test-private-github-token", output.getvalue())

    def test_cli_rejects_changed_head_without_output(self):
        output = StringIO()
        with patch("holding.github_evidence_cli.GitHubEvidenceClient"), \
             patch("holding.github_evidence_cli.observe_full_pr",
                   return_value=FullEvidence(REPO, 230, "b" * 40, 77, 88)):
            with redirect_stdout(output), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as err:
                    main(self.args)
        self.assertEqual(err.exception.code, 2)
        self.assertEqual(output.getvalue(), "")

    def test_read_failure_does_not_echo_token(self):
        with patch("holding.github_evidence_cli.observe_full_pr",
                   side_effect=GitHubEvidenceError("hidden-header-test")):
            errout = StringIO()
            with redirect_stderr(errout), self.assertRaises(SystemExit):
                main(self.args)
        self.assertNotIn("hidden-header-test", errout.getvalue())
        self.assertNotIn("test-private-github-token", errout.getvalue())

    def test_missing_or_symlinked_token_refused(self):
        self.token.unlink()
        with self.assertRaises(GitHubEvidenceError):
            read_token_file(self.token)
        outside = self.root / "outside"
        outside.write_text("test")
        try:
            self.token.symlink_to(outside)
        except OSError:
            self.skipTest("Symlink privilege unavailable")
        with self.assertRaises(GitHubEvidenceError):
            read_token_file(self.token)

    def test_overlarge_and_invalid_token_refused(self):
        self.token.write_bytes(b"x" * 4097)
        with self.assertRaises(GitHubEvidenceError):
            read_token_file(self.token)
        self.token.write_bytes(b"invalid token")
        with self.assertRaises(GitHubEvidenceError):
            read_token_file(self.token)

    @unittest.skipIf(os.name == "nt", "POSIX file-mode gate")
    def test_world_readable_token_is_rejected(self):
        self.token.chmod(0o644)
        with self.assertRaises(GitHubEvidenceError):
            read_token_file(self.token)


if __name__ == "__main__":
    unittest.main()

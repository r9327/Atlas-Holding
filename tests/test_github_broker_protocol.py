import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from holding.github_broker_protocol import (
    BrokerFilePin, BrokerProcessConfig, GitHubBrokerProcessControl,
)
from holding.github_full_evidence import GitHubEvidenceError
from holding.github_mutation_gate import GitHubMutationIntent
from holding.sync_coordinator import MutationFailure


REPO = "r9327/Dof-Atlas"
BRANCH = "holding/lot-1"
SHA = "a" * 40
KEY = "b" * 64


SCRIPT = r'''import json,os,sys
request=json.load(sys.stdin)
if os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"):
    raise SystemExit(9)
action=request["action"]
payload=request["payload"]
if action=="observe_pr":
    result={"repository":payload["repository"],"pr_number":payload["pr_number"],
      "head_branch":"holding/lot-1","head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "state":"open","draft":True,"complete":True,"merged":False,
      "ci_run_id":10,"ci_head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
elif action=="observe_full":
    result={"repository":payload["repository"],"pr_number":payload["pr_number"],
      "candidate_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","run_id":77,
      "job_id":88,"status":"FULL_VERIFIED_BY_GITHUB"}
else:
    result={}
json.dump({"schema_version":1,"request_id":request["request_id"],
  "status":"OK","result":result},sys.stdout,separators=(",",":"),sort_keys=True)
'''


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class GitHubBrokerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.script = self.root / "broker.py"
        self.script.write_text(SCRIPT, encoding="utf-8")
        executable = Path(sys.executable).resolve()
        self.config = BrokerProcessConfig(
            command=(str(executable), str(self.script)),
            file_pins=(
                BrokerFilePin(executable, digest(executable)),
                BrokerFilePin(self.script, digest(self.script)),
            ),
            spool_root=self.root,
        )
        self.control = GitHubBrokerProcessControl(self.config)

    def test_real_broker_roundtrip_returns_strict_pr_and_full_records(self):
        state = self.control.observe_pr(REPO, 42)
        full = self.control.observe_full(REPO, 42)
        self.assertEqual((state.head_sha, state.ci_run_id), (SHA, 10))
        self.assertEqual((full.candidate_sha, full.run_id), (SHA, 77))

    def test_mutation_roundtrip_contains_no_credential(self):
        intent = GitHubMutationIntent(
            "github.pr_ready", REPO, BRANCH, SHA, 42, "c" * 64, None)
        with patch.dict(os.environ, {
                "GITHUB_TOKEN": "must-not-leak", "GH_TOKEN": "also-secret"}):
            self.control.mutate(intent, KEY)

    def test_changed_broker_file_is_refused_before_launch(self):
        self.script.write_text(SCRIPT + "\n# changed", encoding="utf-8")
        with self.assertRaisesRegex(GitHubEvidenceError, "pin mismatch"):
            self.control.observe_pr(REPO, 42)

    def test_broker_crash_after_mutation_request_is_ambiguous(self):
        self.script.write_text("raise SystemExit(7)\n", encoding="utf-8")
        executable = Path(sys.executable).resolve()
        config = BrokerProcessConfig(
            command=(str(executable), str(self.script)),
            file_pins=(
                BrokerFilePin(executable, digest(executable)),
                BrokerFilePin(self.script, digest(self.script)),
            ), spool_root=self.root,
        )
        control = GitHubBrokerProcessControl(config)
        intent = GitHubMutationIntent(
            "github.pr_ready", REPO, BRANCH, SHA, 42, "c" * 64, None)
        with self.assertRaises(MutationFailure) as raised:
            control.mutate(intent, KEY)
        self.assertFalse(raised.exception.outcome_known)

    def test_unpinned_python_code_argument_is_rejected(self):
        executable = Path(sys.executable).resolve()
        config = BrokerProcessConfig(
            command=(str(executable), str(self.script)),
            file_pins=(BrokerFilePin(executable, digest(executable)),),
            spool_root=self.root,
        )
        with self.assertRaises(GitHubEvidenceError):
            GitHubBrokerProcessControl(config)

    def test_partially_pinned_python_package_is_rejected(self):
        helper = self.root / "helper.py"
        helper.write_text("VALUE = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(GitHubEvidenceError, "completely pinned"):
            GitHubBrokerProcessControl(self.config)


if __name__ == "__main__":
    unittest.main()

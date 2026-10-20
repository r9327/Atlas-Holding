"""QA inbox CLI smoke tests: independent signer key handling and actual transition."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.qa_entry import load_verifier_key, run, QaBootstrapError
from holding.qa_inbox import receipt_name
from holding.state_store import StateStore

SHA = "a" * 40
KEY = b"Separate QA supervisor secret, only for integration tests"
REPO = "r9327/Dof-Atlas"


class QaEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "state.sqlite"
        self.evidence = self.root / "signed"
        self.evidence.mkdir()
        self.key = self.root / "qa.key"
        self.key.write_bytes(KEY)
        self.key.chmod(0o600)

    def invoke(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = run(["--db", str(self.db), "--evidence-dir", str(self.evidence),
                        "--key-file", str(self.key), "--once"])
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_missing_database_is_not_silently_created(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke()
        self.assertFalse(self.db.exists())

    def test_single_poll_promotes_only_signed_exact_candidate(self):
        with StateStore(self.db) as store:
            store.add_missions([Mission("first", REPO)])
            store.grant_for_onboarding(StandingGrant(
                REPO, frozenset({"code"}), int(time.time()) + 300))
            self.assertEqual(store.claim_next(online=False).mission_id, "first")
            store.checkpoint("first", SHA)
        self.assertEqual(self.invoke()["missing"], 1)
        artifact = self.evidence / "tests.json"
        artifact.write_text('{"tests": "PASS"}', encoding="utf-8")
        payload = {"schema": 1, "mission_id": "first", "sha": SHA, "verdict": "PASS",
                   "runner": "doctor_atlas", "artifact": artifact.name,
                   "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode()
        signed = {"payload": payload, "mac": hmac.new(KEY, encoded, hashlib.sha256).hexdigest()}
        (self.evidence / receipt_name("first", SHA)).write_text(json.dumps(signed), encoding="utf-8")
        self.assertEqual(self.invoke()["passed"], 1)
        with StateStore(self.db) as store:
            self.assertEqual(store.missions()[0].state, "DONE")
            self.assertEqual(store.missions()[0].evidence_sha, SHA)

    @unittest.skipIf(os.name == "nt", "POSIX owner-only file-mode check")
    def test_world_readable_qa_signing_key_rejected(self):
        self.key.chmod(0o644)
        with self.assertRaises(QaBootstrapError):
            load_verifier_key(self.key)

    def test_short_key_rejected(self):
        self.key.write_bytes(b"short")
        with self.assertRaises(QaBootstrapError):
            load_verifier_key(self.key)


if __name__ == "__main__":
    unittest.main()

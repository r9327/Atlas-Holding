"""Scoped Doctor runner contracts, including exact-SHA and no fake DONE."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from holding.autonomy import Mission, StandingGrant
from holding.qa_scoped import ScopedDoctorRunner, ScopedQaError
from holding.state_store import StateStore, StoreError

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40
BASE = "b" * 40


class _Tree:
    def __init__(self, root):
        self.path = root
        self.base_sha = SHA


class _Git:
    repository = REPO
    def __init__(self, root):
        self.root = root
        self.dirty = False
        self.head = SHA

    def inspect(self, mission_id):
        assert mission_id == "mission"
        tree = _Tree(self.root)
        tree.base_sha = self.head
        return tree

    def _git(self, *args, cwd=None):
        if args[0] == "rev-parse":
            return BASE
        if args[0] == "status":
            return " M app/code.py" if self.dirty else ""
        raise AssertionError("Unexpected trusted Git command")


def _report(*, sha=SHA, base=BASE, status="READY_TO_RUN_SCOPED",
            execution="SCOPED_TESTS_PASS", modules=None, exit_code=0,
            certified=False):
    selected = ["tests.test_code"] if modules is None else modules
    return {
        "candidate_sha": sha, "base_sha": base, "status": status,
        "certified": certified, "phase_certified": False,
        "full_suite_waived": False, "test_modules": selected,
        "execution": {"status": execution, "exit_code": exit_code,
                      "executed_modules": selected},
    }


class ScopedDoctorRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git = _Git(self.root)
        self.runner = ScopedDoctorRunner(self.git, Path(sys.executable).resolve())
        self.plan = type("Advice", (), {"status": "READY_TO_RUN_SCOPED",
                                        "report_sha256": "c" * 64})()
        self.doctor_patch = patch("holding.qa_scoped.DoctorPlanner")
        self.doctor = self.doctor_patch.start()
        self.addCleanup(self.doctor_patch.stop)
        self.doctor.return_value.plan.return_value = self.plan

    def run_with_report(self, report, *, process_exit=0):
        class Completed:
            returncode = process_exit
        def execute(argv, *, cwd, stdin, stdout, stderr, timeout, check, env):
            self.assertEqual(argv[-1], "--run")
            self.assertEqual(argv[argv.index("--expected-sha") + 1], SHA)
            self.assertEqual(argv[argv.index("--base-ref") + 1], BASE)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertFalse(check)
            self.assertEqual(cwd, self.root)
            stdout.write(json.dumps(report).encode())
            return Completed()
        with patch("holding.qa_scoped.subprocess.run", side_effect=execute):
            return self.runner.run("mission", SHA)

    def test_successful_scoped_test_is_advisory_not_certified(self):
        result = self.run_with_report(_report())
        self.assertEqual(result.status, "SCOPED_TESTS_PASS")
        self.assertTrue(result.advisory_only)
        self.assertEqual(result.candidate_sha, SHA)
        self.assertEqual(result.modules, ("tests.test_code",))

    def test_full_required_never_dispatches_tests(self):
        self.plan.status = "FULL_REQUIRED"
        with patch("holding.qa_scoped.subprocess.run") as execute:
            result = self.runner.run("mission", SHA)
        self.assertEqual(result.status, "FULL_REQUIRED")
        execute.assert_not_called()

    def test_wrong_sha_rejected(self):
        with self.assertRaises(ScopedQaError):
            self.run_with_report(_report(sha="f" * 40))
        with self.assertRaises(ScopedQaError):
            self.runner.run("mission", "not-a-sha")

    def test_fake_certification_claim_rejected(self):
        with self.assertRaises(ScopedQaError):
            self.run_with_report(_report(certified=True))

    def test_zero_exit_without_executed_modules_is_not_pass(self):
        result = self.run_with_report(_report(modules=[]))
        self.assertEqual(result.status, "SCOPED_TESTS_FAIL")

    def test_failed_tests_are_not_pass(self):
        result = self.run_with_report(_report(
            execution="BLOCKED", exit_code=1), process_exit=1)
        self.assertEqual(result.status, "SCOPED_TESTS_FAIL")

    def test_mutated_worktree_rejects_scoped_success(self):
        self.git.dirty = True
        self.assertEqual(self.run_with_report(_report()).status, "SCOPED_TESTS_FAIL")

    def test_preflight_error_prevents_test_execution(self):
        self.doctor.return_value.plan.side_effect = RuntimeError("not an allowed preflight")
        with patch("holding.qa_scoped.subprocess.run") as execute:
            with self.assertRaises(RuntimeError):
                self.runner.run("mission", SHA)
        execute.assert_not_called()

    def test_pending_advisory_persists_without_promoting_done(self):
        db_path = self.root / "state.sqlite"
        with StateStore(db_path) as state:
            state.add_missions([Mission("mission", REPO)])
            state.grant_for_onboarding(StandingGrant(
                REPO, frozenset({"code"}), int(time.time()) + 1000))
            self.assertEqual(state.claim_next(online=False).mission_id, "mission")
            state.checkpoint("mission", SHA)
            with patch("holding.qa_scoped.subprocess.run",
                       side_effect=self._write_successful_report):
                result = self.runner.run_pending(state, "mission")
            self.assertEqual(result.status, "SCOPED_TESTS_PASS")
            self.assertEqual(state.missions()[0].state, "QA_PENDING")
            self.assertEqual(state.missions()[0].evidence_sha, "")
            advisory = state.db.execute(
                "SELECT status,candidate_sha FROM scoped_qa_advisories WHERE mission_id=?",
                ("mission",)).fetchone()
            self.assertEqual(tuple(advisory), ("SCOPED_TESTS_PASS", SHA))

    @staticmethod
    def _write_successful_report(argv, *, stdout, **kwargs):
        stdout.write(json.dumps(_report()).encode())
        return type("Completed", (), {"returncode": 0})()

    def test_without_pending_exact_mission_no_test_process_launches(self):
        with StateStore(self.root / "state.sqlite") as state:
            with patch("holding.qa_scoped.subprocess.run") as run:
                with self.assertRaises(StoreError):
                    self.runner.run_pending(state, "mission")
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

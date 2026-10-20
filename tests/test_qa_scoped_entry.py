"""Scoped QA CLI must reject untrusted paths and expose advisory outcomes only."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.qa_scoped import ScopedQaResult
from holding.qa_scoped_entry import main

SHA = "a" * 40
BASE = "b" * 40


class ScopedEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "state.sqlite"
        self.db.write_bytes(b"fixture")
        self.arguments = [
            "--db", str(self.db),
            "--canonical-root", str(self.root),
            "--workspace", str(self.root / "worktrees"),
            "--repository", "r9327/Dof-Atlas",
            "--interpreter", str(self.root / "python.exe"),
            "--mission", "mission",
        ]

    def test_relative_database_rejected_before_git_and_tests(self):
        args = self.arguments.copy()
        args[args.index("--db") + 1] = "state.sqlite"
        with patch("holding.qa_scoped_entry.GitWorktrees") as git:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(args)
            git.assert_not_called()

    def test_nonexistent_database_not_created(self):
        self.db.unlink()
        with patch("holding.qa_scoped_entry.GitWorktrees") as git:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(self.arguments)
            git.assert_not_called()
        self.assertFalse(self.db.exists())

    def test_advisory_pass_returns_json_without_certification(self):
        report = ScopedQaResult(SHA, BASE, "SCOPED_TESTS_PASS",
                                ("tests.test_code",), "c" * 64)
        output = StringIO()
        with patch("holding.qa_scoped_entry.GitWorktrees") as git, \
             patch("holding.qa_scoped_entry.StateStore") as store, \
             patch("holding.qa_scoped_entry.ScopedDoctorRunner") as runner:
            runner.return_value.run_pending.return_value = report
            with redirect_stdout(output):
                self.assertEqual(main(self.arguments), 0)
            git.return_value.inspect_canonical.assert_called_once()
            runner.return_value.run_pending.assert_called_once()
        decoded = json.loads(output.getvalue())
        self.assertEqual(decoded["status"], "SCOPED_TESTS_PASS")
        self.assertTrue(decoded["advisory_only"])

    def test_watch_once_uses_persistent_dispatch_not_direct_worker(self):
        args = self.arguments.copy()
        index = args.index("--mission")
        args[index:index + 2] = ["--watch", "--once"]
        outcome = type("Result", (), {
            "action": "SCOPED_TESTS_PASS", "mission_id": "mission",
            "reason": "Scoped advisory recorded"})()
        result = StringIO()
        with patch("holding.qa_scoped_entry.GitWorktrees"), \
             patch("holding.qa_scoped_entry.StateStore"), \
             patch("holding.qa_scoped_entry.ScopedDoctorRunner") as runner, \
             patch("holding.qa_scoped_entry.ScopedQaDispatcher") as dispatch:
            dispatch.return_value.tick.return_value = outcome
            with redirect_stdout(result):
                self.assertEqual(main(args), 0)
            dispatch.return_value.tick.assert_called_once()
            runner.return_value.run_pending.assert_not_called()
        payload = json.loads(result.getvalue())
        self.assertEqual(payload["action"], "SCOPED_TESTS_PASS")

    def test_watch_once_full_required_exits_nonzero(self):
        args = self.arguments.copy()
        index = args.index("--mission")
        args[index:index + 2] = ["--watch", "--once"]
        outcome = type("Result", (), {
            "action": "FULL_REQUIRED", "mission_id": "mission",
            "reason": "Needs independent certification"})()
        with patch("holding.qa_scoped_entry.GitWorktrees"), \
             patch("holding.qa_scoped_entry.StateStore"), \
             patch("holding.qa_scoped_entry.ScopedDoctorRunner"), \
             patch("holding.qa_scoped_entry.ScopedQaDispatcher") as dispatch:
            dispatch.return_value.tick.return_value = outcome
            with redirect_stdout(StringIO()):
                self.assertEqual(main(args), 1)

    def test_full_required_returns_nonzero_not_fake_success(self):
        report = ScopedQaResult(SHA, BASE, "FULL_REQUIRED", (), "d" * 64)
        with patch("holding.qa_scoped_entry.GitWorktrees"), \
             patch("holding.qa_scoped_entry.StateStore"), \
             patch("holding.qa_scoped_entry.ScopedDoctorRunner") as runner:
            runner.return_value.run_pending.return_value = report
            with redirect_stdout(StringIO()):
                self.assertEqual(main(self.arguments), 1)


if __name__ == "__main__":
    unittest.main()

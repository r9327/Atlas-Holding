import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from holding.atlas_doctor_runner import (
    AtlasDoctorRunner, DoctorRunnerError, WorktreeIdentity,
)
from holding.bounded_process import ProcessResult


SHA = "a" * 40
PATHS = ("app/core/example.py",)


def doctor_report(*, action="validate", dirty=True):
    return {
        "schema_version": 1, "protocol": "atlas-doctor-agent/v1",
        "action": action, "status": "PASS" if action != "plan" else "READY",
        "exit_code": 0, "exact_sha": SHA, "base_ref": "HEAD",
        "worktree": {"dirty": dirty, "dirty_digest": "b" * 64 if dirty else ""},
        "edit_contract": {
            "requested_paths": list(PATHS), "planned_paths": list(PATHS),
            "expansion_requires_replan": True,
        },
        "selected_checks": {
            "graph_required": False, "graph_status": "OPTIONAL",
            "tests": ["tests.test_example"], "rules": [],
        },
        "certification": {
            "status": "SCOPED_VALIDATED" if action == "validate" else "NOT_RUN",
            "full_certified": False, "release_certified": False,
            "exact_sha_required": True,
        },
        **({} if action == "plan" else {"execution": {
            "status": "PASS", "tests_executed": ["tests.test_example"],
        }}),
    }


class AtlasDoctorRunnerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        (self.root / "tools").mkdir()
        (self.root / "tools" / "atlas_doctor.py").write_text("# trusted fixture\n")
        (self.root / "AI_ENTRYPOINT.md").write_text("fixture\n")
        git = Path(shutil.which("git") or "").resolve()
        self.runner = AtlasDoctorRunner(
            self.root, Path(sys.executable).resolve(), git,
            timeout_seconds=30, max_tests=4,
        )
        self.git_patch = patch.object(
            self.runner, "_git", return_value=(SHA + "\n").encode(),
        )
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
        self.state_patch = patch.object(
            self.runner, "_worktree_identity",
            return_value=WorktreeIdentity(True, "b" * 64),
        )
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_fixed_doctor_command_and_secret_free_environment(self):
        raw = json.dumps(doctor_report()).encode()
        with patch("holding.atlas_doctor_runner.run_bounded",
                   return_value=ProcessResult(0, raw, b"")) as execute:
            passport = self.runner.run("validate", PATHS, candidate_sha=SHA)
        self.assertEqual(passport.status, "VALIDATED_LOCAL")
        argv = execute.call_args.args[0]
        self.assertEqual(argv[:6], (
            str(Path(sys.executable).resolve()), "-B", "-m",
            "tools.atlas_doctor", "--json", "agent",
        ))
        self.assertIn("validate", argv)
        self.assertNotIn("GITHUB_TOKEN", execute.call_args.kwargs["environment"])
        self.assertEqual(execute.call_args.kwargs["stdout_limit"], 1024 * 1024)

    def test_refusal_report_consumed_once_without_rerunning_process(self):
        refusal = doctor_report()
        refusal["status"] = "FAIL"
        refusal["exit_code"] = 2
        refusal["certification"]["status"] = "NOT_RUN"
        refusal["execution"]["status"] = "FAIL"
        raw = json.dumps(refusal).encode()
        with patch("holding.atlas_doctor_runner.run_bounded",
                   return_value=ProcessResult(2, raw, b"")) as execute:
            passport = self.runner.run(
                "validate", PATHS, candidate_sha=SHA,
                capture_verified_refusal=True,
            )
        self.assertEqual(passport.status, "FAIL")
        self.assertEqual(execute.call_count, 1)
        observed = self.runner.take_verified_diagnostic(
            action="validate", candidate_sha=SHA,
            report_sha256=passport.report_sha256,
        )
        self.assertEqual(observed, raw)
        with self.assertRaises(DoctorRunnerError):
            self.runner.take_verified_diagnostic(
                action="validate", candidate_sha=SHA,
                report_sha256=passport.report_sha256,
            )

    def test_wrong_diagnostic_identity_clears_cached_bytes(self):
        refusal = doctor_report()
        refusal["status"] = "FAIL"
        refusal["exit_code"] = 2
        refusal["certification"]["status"] = "NOT_RUN"
        refusal["execution"]["status"] = "FAIL"
        raw = json.dumps(refusal).encode()
        with patch("holding.atlas_doctor_runner.run_bounded",
                   return_value=ProcessResult(2, raw, b"")):
            passport = self.runner.run(
                "validate", PATHS, candidate_sha=SHA,
                capture_verified_refusal=True,
            )
        with self.assertRaises(DoctorRunnerError):
            self.runner.take_verified_diagnostic(
                action="check", candidate_sha=SHA,
                report_sha256=passport.report_sha256,
            )
        with self.assertRaises(DoctorRunnerError):
            self.runner.take_verified_diagnostic(
                action="validate", candidate_sha=SHA,
                report_sha256=passport.report_sha256,
            )

    def test_success_does_not_retain_report(self):
        raw = json.dumps(doctor_report()).encode()
        with patch("holding.atlas_doctor_runner.run_bounded",
                   return_value=ProcessResult(0, raw, b"")):
            passport = self.runner.run(
                "validate", PATHS, candidate_sha=SHA,
                capture_verified_refusal=True,
            )
        with self.assertRaises(DoctorRunnerError):
            self.runner.take_verified_diagnostic(
                action="validate", candidate_sha=SHA,
                report_sha256=passport.report_sha256,
            )

    def test_checkout_sha_and_state_are_rechecked(self):
        self.runner._git.side_effect = [(SHA + "\n").encode(), ("c" * 40 + "\n").encode()]
        with patch("holding.atlas_doctor_runner.run_bounded",
                   return_value=ProcessResult(0, json.dumps(doctor_report()).encode(), b"")):
            with self.assertRaisesRegex(DoctorRunnerError, "changed"):
                self.runner.run("validate", PATHS, candidate_sha=SHA)

    def test_agent_cannot_inject_options_paths_or_refs(self):
        bad = (("--run-tests",), ("../outside",), ("C:/outside",), ("bad\x00path",))
        with patch("holding.atlas_doctor_runner.run_bounded") as execute:
            for paths in bad:
                with self.subTest(paths=paths), self.assertRaises(DoctorRunnerError):
                    self.runner.run("validate", paths, candidate_sha=SHA)
            execute.assert_not_called()
        with self.assertRaises(DoctorRunnerError):
            self.runner.run("validate", PATHS, candidate_sha=SHA, base_ref="--exec")
        with self.assertRaises(DoctorRunnerError):
            self.runner.run("validate", PATHS, candidate_sha=SHA, run_tests=True)

    def test_clean_plan_uses_empty_canonical_digest(self):
        self.runner._worktree_identity.return_value = WorktreeIdentity(False, "")
        raw = json.dumps(doctor_report(action="plan", dirty=False)).encode()
        with patch("holding.atlas_doctor_runner.run_bounded",
                   return_value=ProcessResult(0, raw, b"")):
            passport = self.runner.run("plan", PATHS, candidate_sha=SHA)
        self.assertEqual((passport.status, passport.dirty_digest), ("PLANNED_LOCAL", ""))

    def test_invalid_or_stale_report_is_not_a_passport(self):
        payload = doctor_report()
        payload["exact_sha"] = "c" * 40
        with patch("holding.atlas_doctor_runner.run_bounded",
                   return_value=ProcessResult(0, json.dumps(payload).encode(), b"")):
            with self.assertRaisesRegex(DoctorRunnerError, "invalid evidence"):
                self.runner.run("validate", PATHS, candidate_sha=SHA)

    def test_dirty_digest_matches_atlas_text_normalization(self):
        self.state_patch.stop()
        untracked = self.root / "new.txt"
        untracked.write_bytes(b"new content\n")
        tracked_diff = b"diff --git a/a b/a\n+line\n\n"
        self.runner._git.side_effect = [
            b" M a\n?? new.txt\n", tracked_diff, b"new.txt\x00",
        ]
        identity = self.runner._worktree_identity()
        expected = hashlib.sha256()
        expected.update(tracked_diff.decode().strip().encode())
        expected.update(b"new.txt")
        expected.update(b"new content\n")
        self.assertEqual(identity, WorktreeIdentity(True, expected.hexdigest()))


if __name__ == "__main__":
    unittest.main()

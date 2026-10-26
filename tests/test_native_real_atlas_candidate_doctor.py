"""Portable fail-closed unit tests for the real disposable Doctor integration."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from holding.atlas_doctor_evidence import LocalPassport
from tests.native_real_atlas_candidate_doctor import (
    _candidate_summary, _verify_advisory, _same_canonical_checkout, _git,
    _review_diagnostics,
    LEVELS, SCOPE,
)

SOURCE = "a" * 40
CANDIDATE = "b" * 40


def passport(stage: str, *, state: str | None = None,
             selected: tuple[str, ...] = ()) -> LocalPassport:
    default = {"plan": "PLANNED_LOCAL", "check": "CHECKED_LOCAL",
               "validate": "VALIDATED_LOCAL"}[stage]
    return LocalPassport(
        candidate_sha=CANDIDATE, action=stage, status=state or default,
        report_sha256="c" * 64,
        dirty=False, dirty_digest="", graph_required=False,
        graph_status="OPTIONAL", tests_executed=selected, certified_full=False,
    )


class NativeCandidateDoctorTests(unittest.TestCase):
    def test_real_git_root_is_verified_as_resolved_path_not_raw_text(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(strict=True)
            self.assertTrue(_same_canonical_checkout(root, str(root)))
            self.assertTrue(_same_canonical_checkout(root, str(root) + "/."))
            self.assertFalse(_same_canonical_checkout(root, "../outside"))
            self.assertFalse(_same_canonical_checkout(root, ""))
            self.assertFalse(_same_canonical_checkout(root, str(root.parent)))

    def test_local_git_disables_network_prompt_and_lfs_smudge(self):
        completed = Mock(returncode=0, stdout="safe", stderr="")
        with patch("tests.native_real_atlas_candidate_doctor.subprocess.run",
                   return_value=completed) as spawn:
            self.assertEqual(_git(Path("C:/dummy"), "status"), "safe")
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GIT_LFS_SKIP_SMUDGE"], "1")
        self.assertEqual(spawn.call_args.args[0][0], "git")

    def test_all_three_valid_advisory_shapes_remain_uncertified(self):
        self.assertEqual(LEVELS, ("plan", "check", "validate"))
        self.assertEqual(SCOPE, ("README.md",))
        stages = []
        for stage, status, selected in (
            ("plan", "PLANNED_LOCAL", ()),
            ("check", "CHECKED_LOCAL", ("tests.test_readme",)),
            ("validate", "VALIDATED_LOCAL", ()),
        ):
            row = _verify_advisory(
                passport(stage, state=status, selected=selected),
                stage=stage, sha=CANDIDATE)
            self.assertIs(row["local_advisory_only"], True)
            stages.append(row)
        summary = _candidate_summary(
            source_sha=SOURCE, candidate_sha=CANDIDATE, stages=stages)
        self.assertEqual(summary["modified_paths"], ["README.md"])
        self.assertIs(summary["model_generated_candidate"], False)
        for claim in ("independent_qa_pass", "doctor_full_certified",
                      "github_push_permitted", "release_ready"):
            self.assertIs(summary[claim], False)

    def test_review_does_not_pretend_to_have_run_tests(self):
        for stage in LEVELS:
            record = _verify_advisory(
                passport(stage, state="REVIEW"), stage=stage, sha=CANDIDATE)
            self.assertEqual(record["state"], "REVIEW")
            self.assertEqual(record["test_modules_reported"], 0)

    def test_clean_exact_sha_is_mandatory(self):
        initial = passport("plan")
        for field, value in (
            ("candidate_sha", SOURCE), ("action", "validate"),
            ("report_sha256", "invalid"), ("dirty", True),
            ("dirty_digest", "d" * 64), ("certified_full", True),
            ("tests_executed", ("tests.bad",)),
        ):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                _verify_advisory(
                    replace(initial, **{field: value}),
                    stage="plan", sha=CANDIDATE)

    def test_checker_must_not_claim_pass_without_a_test(self):
        with self.assertRaisesRegex(RuntimeError, "without selected"):
            _verify_advisory(
                passport("check"), stage="check", sha=CANDIDATE)

    def test_no_known_disallowed_stage_status_becomes_pass(self):
        cases = (
            ("plan", "VALIDATED_LOCAL"),
            ("plan", "BLOCKED"),
            ("check", "PLANNED_LOCAL"),
            ("validate", "CHECKED_LOCAL"),
        )
        for stage, state in cases:
            with self.subTest(stage=stage, state=state), self.assertRaises(RuntimeError):
                _verify_advisory(
                    passport(stage, state=state), stage=stage, sha=CANDIDATE)

    def test_authenticated_review_reasons_are_bounded_and_never_a_pass(self):
        envelope = {
            "action": "check", "status": "REVIEW",
            "execution": {"status": "PLANNED", "tests_executed": False},
            "selected_checks": {"tests": []},
            "next_actions": ["No runnable focused tests were selected."],
        }
        observed = _review_diagnostics(envelope, stage="check")
        self.assertIn("execution=PLANNED", observed)
        self.assertIn("selected_test_modules=0", observed)
        self.assertNotIn("PASS", " ".join(observed))
        envelope["status"] = "PASS"
        with self.assertRaises(RuntimeError):
            _review_diagnostics(envelope, stage="check")

    def test_review_diagnostics_reject_unbounded_or_incomplete_report(self):
        valid = {
            "action": "validate", "status": "REVIEW",
            "execution": {"status": "REVIEW", "tests_executed": []},
            "selected_checks": {"tests": ["tests.test_example"]},
            "next_actions": ["Review policy."],
        }
        for field, value in (
            ("next_actions", ["a" * 1501]),
            ("next_actions", ["x"] * 11),
            ("execution", {}),
            ("selected_checks", {"tests": [True]}),
            ("selected_checks", {"tests": []}),
        ):
            if field == "selected_checks" and value == {"tests": []}:
                # Empty selected tests is a legitimate evidence-bearing REVIEW.
                self.assertIn("selected_test_modules=0",
                              _review_diagnostics({**valid, field: value},
                                                  stage="validate"))
                continue
            with self.subTest(field=field, value=str(value)[:30]):
                with self.assertRaises(RuntimeError):
                    _review_diagnostics({**valid, field: value},
                                        stage="validate")

    def test_a_review_plan_or_failed_later_stage_cannot_seal(self):
        good_stages = [
            _verify_advisory(passport("plan"), stage="plan", sha=CANDIDATE),
            _verify_advisory(passport("check", state="REVIEW"),
                             stage="check", sha=CANDIDATE),
            _verify_advisory(passport("validate", state="REVIEW"),
                             stage="validate", sha=CANDIDATE),
        ]
        for position, state in ((0, "REVIEW"), (1, "BLOCKED"),
                                (1, "FAIL"), (2, "FAIL")):
            with self.subTest(position=position, state=state):
                stages = [dict(x) for x in good_stages]
                stages[position]["state"] = state
                with self.assertRaises(RuntimeError):
                    _candidate_summary(source_sha=SOURCE,
                                       candidate_sha=CANDIDATE, stages=stages)

    def test_incomplete_or_reordered_qa_session_never_seals(self):
        stages = [
            _verify_advisory(passport("plan"), stage="plan", sha=CANDIDATE),
            _verify_advisory(passport("check", state="REVIEW"),
                             stage="check", sha=CANDIDATE),
            _verify_advisory(passport("validate", state="REVIEW"),
                             stage="validate", sha=CANDIDATE),
        ]
        for selection in (stages[:1], stages[1:], list(reversed(stages))):
            with self.subTest(selection=selection), self.assertRaises(RuntimeError):
                _candidate_summary(source_sha=SOURCE,
                                   candidate_sha=CANDIDATE, stages=selection)
        with self.assertRaises(RuntimeError):
            _candidate_summary(
                source_sha=SOURCE, candidate_sha=SOURCE, stages=stages)

    def test_check_and_validate_may_be_rejected_but_not_certified(self):
        for stage in ("check", "validate"):
            for status in ("REVIEW", "BLOCKED", "FAIL"):
                with self.subTest(stage=stage, status=status):
                    item = _verify_advisory(
                        passport(stage, state=status), stage=stage,
                        sha=CANDIDATE)
                    self.assertNotIn(item["state"], ("PASS", "DONE"))
                    self.assertIs(item["local_advisory_only"], True)


if __name__ == "__main__":
    unittest.main()

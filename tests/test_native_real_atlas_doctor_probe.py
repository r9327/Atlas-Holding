"""Portable contract tests: a real Doctor plan never grants QA/full/release."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.atlas_doctor_evidence import LocalPassport
from tests.native_real_atlas_doctor_probe import (
    _approved_scope, _verify_local_plan, _verify_pinned_scope_decision,
    _evidence_receipt, _seal_doctor_receipt,
)

SHA = "a" * 40


def advisory(*, status: str = "PLANNED_LOCAL") -> LocalPassport:
    return LocalPassport(
        candidate_sha=SHA,
        action="plan",
        status=status,
        report_sha256="b" * 64,
        dirty=False,
        dirty_digest="",
        graph_required=False,
        graph_status="OPTIONAL",
        tests_executed=(),
        certified_full=False,
    )


class RealAtlasDoctorProbeTests(unittest.TestCase):
    def test_readonly_exact_sha_plan_is_nonrelease(self):
        self.assertEqual(
            _verify_local_plan(advisory(), SHA),
            "NONRELEASE_REAL_ATLAS_DOCTOR_PLAN_READY",
        )

    def test_human_review_is_explicitly_not_ready(self):
        self.assertEqual(
            _verify_local_plan(advisory(status="REVIEW"), SHA),
            "NONRELEASE_REAL_ATLAS_DOCTOR_REVIEW_NOT_READY",
        )

    def test_stale_dirty_or_untrusted_result_is_rejected(self):
        for field, value in (
            ("candidate_sha", "c" * 40),
            ("action", "validate"),
            ("status", "PASS"),
            ("status", "BLOCKED"),
            ("dirty", True),
            ("dirty_digest", "d" * 64),
            ("tests_executed", ("test_hidden",)),
            ("certified_full", True),
            ("report_sha256", "unverified"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(RuntimeError):
                    _verify_local_plan(replace(advisory(), **{field: value}), SHA)

    def test_broken_expected_sha_is_rejected(self):
        for wrong in ("", "deadbeef", SHA.upper(), "a" * 41):
            with self.subTest(wrong=wrong), self.assertRaises(RuntimeError):
                _verify_local_plan(advisory(), wrong)

    def test_scope_is_bound_to_exact_allowlisted_Atlas_path(self):
        self.assertEqual(_approved_scope("unowned"), ("app/constants.py",))
        self.assertEqual(_approved_scope("owned_home"),
                         ("app/pages/home_page.py",))

    def test_arbitrary_or_path_traversal_doctor_scope_is_rejected(self):
        for scope in ("../outside", "app/pages/home_page.py",
                      "tools/doctor_certification.py", "", "--run-tests",
                      "Owned_Home", None):
            with self.subTest(scope=scope), self.assertRaises(RuntimeError):
                _approved_scope(scope)

    def test_unowned_source_must_never_receive_ready_to_edit(self):
        _verify_pinned_scope_decision("unowned", "REVIEW")
        with self.assertRaisesRegex(RuntimeError, "unexpectedly authorized"):
            _verify_pinned_scope_decision("unowned", "PLANNED_LOCAL")

    def test_owned_source_does_not_automatically_guarantee_ready(self):
        for status in ("REVIEW", "PLANNED_LOCAL"):
            with self.subTest(status=status):
                _verify_pinned_scope_decision("owned_home", status)
        with self.assertRaises(RuntimeError):
            _verify_pinned_scope_decision("owned_home", "VALIDATED_LOCAL")

    def test_machine_receipt_never_asserts_authority(self):
        payload = _evidence_receipt("unowned", SHA, advisory(status="REVIEW"))
        self.assertEqual(payload["path"], "app/constants.py")
        self.assertEqual(payload["status"], "REVIEW")
        self.assertEqual(payload["source_sha"], SHA)
        for field in ("doctor_tests_executed", "doctor_full_certified",
                      "github_push_permitted", "release_ready"):
            self.assertIs(payload[field], False)
        self.assertEqual(
            _evidence_receipt("owned_home", SHA, advisory())["scope"],
            "owned_home")

    def test_seal_local_plan_proof_is_exclusive_and_bound_to_runner(self):
        payload = _evidence_receipt("unowned", SHA, advisory(status="REVIEW"))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            target = directory / "proof.json"
            with patch.dict(os.environ, {"RUNNER_TEMP": str(directory)}):
                digest = _seal_doctor_receipt(target, payload)
                self.assertEqual(len(digest), 64)
                self.assertEqual(json.loads(target.read_text()), payload)
                with self.assertRaises(FileExistsError):
                    _seal_doctor_receipt(target, payload)
                self.assertEqual(json.loads(target.read_text()), payload)
                with self.assertRaises(RuntimeError):
                    _seal_doctor_receipt(directory.parent / "outside.json", payload)

    def test_seal_rejects_fake_doctor_or_release_claims(self):
        good = _evidence_receipt("unowned", SHA, advisory(status="REVIEW"))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            with patch.dict(os.environ, {"RUNNER_TEMP": str(directory)}):
                for field in ("doctor_tests_executed", "doctor_full_certified",
                              "github_push_permitted", "release_ready"):
                    receipt = dict(good)
                    receipt[field] = True
                    target = directory / (field + ".json")
                    with self.subTest(field=field), self.assertRaises(RuntimeError):
                        _seal_doctor_receipt(target, receipt)
                    self.assertFalse(target.exists())

    def test_nonpassport_result_is_rejected(self):
        with self.assertRaises(RuntimeError):
            _verify_local_plan({"status": "PASS"}, SHA)


if __name__ == "__main__":
    unittest.main()

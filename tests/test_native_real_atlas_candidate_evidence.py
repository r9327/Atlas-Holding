"""Independent verifier rejects local Doctor claims and ambiguous inputs."""
from __future__ import annotations

import copy
import json
import unittest

from tests.native_real_atlas_candidate_evidence import (
    EvidenceError, verify_disposable_candidate,
)

BASE = "a" * 40
HEAD = "b" * 40


def good() -> dict:
    return {
        "schema": 1,
        "kind": "NONRELEASE_REAL_ATLAS_DISPOSABLE_CANDIDATE",
        "source_repository": "r9327/Dof-Atlas",
        "source_sha": BASE,
        "candidate_sha": HEAD,
        "modified_paths": ["README.md"],
        "stages": [
            {"stage": "plan", "state": "PLANNED_LOCAL",
             "doctor_report_sha256": "c" * 64,
             "test_modules_reported": 0, "local_advisory_only": True},
            {"stage": "check", "state": "CHECKED_LOCAL",
             "doctor_report_sha256": "d" * 64,
             "test_modules_reported": 1, "local_advisory_only": True},
            {"stage": "validate", "state": "VALIDATED_LOCAL",
             "doctor_report_sha256": "e" * 64,
             "test_modules_reported": 0, "local_advisory_only": True},
        ],
        "independent_qa_pass": False,
        "doctor_full_certified": False,
        "github_push_permitted": False,
        "release_ready": False,
        "model_generated_candidate": False,
    }


def encoded(data: dict) -> bytes:
    return (json.dumps(data, sort_keys=True) + "\n").encode()


class CandidateEvidenceVerifierTests(unittest.TestCase):
    def test_valid_local_doc_advisory_is_explicitly_nonrelease(self):
        self.assertEqual(
            len(verify_disposable_candidate(encoded(good()), source_sha=BASE)),
            64)

    def test_fail_closed_if_claiming_full_or_github_authority(self):
        for name in ("independent_qa_pass", "doctor_full_certified",
                     "github_push_permitted", "release_ready",
                     "model_generated_candidate"):
            with self.subTest(field=name):
                payload = good()
                payload[name] = True
                with self.assertRaises(EvidenceError):
                    verify_disposable_candidate(encoded(payload), source_sha=BASE)

    def test_wrong_source_sha_and_missing_stages(self):
        for field, value in (
            ("source_sha", HEAD), ("candidate_sha", BASE),
            ("source_repository", "other/repository"),
            ("modified_paths", ["app/constants.py"]),
            ("stages", []), ("schema", True),
        ):
            with self.subTest(field=field):
                payload = good()
                payload[field] = value
                with self.assertRaises(EvidenceError):
                    verify_disposable_candidate(encoded(payload), source_sha=BASE)

    def test_doctor_cannot_forge_success_without_selected_tests(self):
        changes = (
            (0, "state", "VALIDATED_LOCAL"),
            (0, "test_modules_reported", 1),
            (1, "state", "DONE"),
            (1, "test_modules_reported", 0),
            (2, "doctor_report_sha256", "not a digest"),
            (2, "local_advisory_only", False),
            (2, "stage", "check"),
        )
        for i, field, value in changes:
            with self.subTest(i=i, field=field):
                payload = copy.deepcopy(good())
                payload["stages"][i][field] = value
                with self.assertRaises(EvidenceError):
                    verify_disposable_candidate(encoded(payload), source_sha=BASE)

    def test_broken_json_and_duplicate_keys(self):
        for raw in (
            b"not json", b"", b"{\"schema\":1,\"schema\":1}",
            b"x" * 4097, b"{}",
        ):
            with self.subTest(length=len(raw)), self.assertRaises(EvidenceError):
                verify_disposable_candidate(raw, source_sha=BASE)

    def test_extra_unknown_authority_field_is_rejected(self):
        payload = good()
        payload["release_certified"] = True
        with self.assertRaises(EvidenceError):
            verify_disposable_candidate(encoded(payload), source_sha=BASE)

    def test_human_review_is_allowed_but_never_rebranded(self):
        payload = good()
        payload["stages"][1]["state"] = "REVIEW"
        payload["stages"][1]["test_modules_reported"] = 0
        payload["stages"][2]["state"] = "REVIEW"
        self.assertEqual(
            len(verify_disposable_candidate(encoded(payload), source_sha=BASE)),
            64)


if __name__ == "__main__":
    unittest.main()

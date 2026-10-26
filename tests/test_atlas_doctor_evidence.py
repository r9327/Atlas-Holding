import json
import unittest

from holding.atlas_doctor_evidence import (
    DoctorEvidenceError, MAX_REPORT_BYTES, consume_doctor_report,
)


SHA = "a" * 40
PATHS = ("app/core/example.py",)


def report(*, action="validate", status="PASS", dirty=True,
           structural=False):
    exit_code = 0 if status in {"READY", "PASS"} else 1 if status == "REVIEW" else 2
    payload = {
        "schema_version": 1,
        "protocol": "atlas-doctor-agent/v1",
        "action": action,
        "status": status,
        "exit_code": exit_code,
        "exact_sha": SHA,
        "base_ref": "HEAD",
        "worktree": {"dirty": dirty, "dirty_digest": "b" * 64 if dirty else ""},
        "edit_contract": {
            "requested_paths": list(PATHS),
            "planned_paths": list(PATHS),
            "expansion_requires_replan": True,
            "protected_paths": [".github/workflows/"],
        },
        "selected_checks": {
            "risk": "MEDIUM", "level": "FAST", "integrity_mode": "FAST",
            "graph_required": structural,
            "graph_status": "PASS" if structural else "OPTIONAL",
            "tests": ["tests.test_example"],
            "rules": ["DEVELOPMENT_GUARDRAILS.md"],
        },
        "certification": {
            "status": "SCOPED_VALIDATED" if action == "validate" else "NOT_RUN",
            "full_certified": False,
            "release_certified": False,
            "exact_sha_required": True,
        },
    }
    if action in {"check", "validate"}:
        payload["execution"] = {
            "status": status,
            "checks": [],
            "tests_executed": ["tests.test_example"],
            "duration_ms": 12,
        }
    return payload


def consume(payload, *, exit_code=None, structural=False):
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return consume_doctor_report(
        raw,
        observed_exit_code=payload["exit_code"] if exit_code is None else exit_code,
        expected_sha=SHA,
        expected_action=payload["action"],
        expected_base_ref="HEAD",
        expected_paths=PATHS,
        expected_dirty=payload["worktree"]["dirty"],
        expected_dirty_digest="b" * 64 if payload["worktree"]["dirty"] else "",
        structural=structural,
    )


class DoctorEvidenceTests(unittest.TestCase):
    def test_scoped_validate_is_local_and_never_certified_full(self):
        passport = consume(report())
        self.assertEqual(passport.status, "VALIDATED_LOCAL")
        self.assertEqual(passport.candidate_sha, SHA)
        self.assertEqual(passport.tests_executed, ("tests.test_example",))
        self.assertFalse(passport.certified_full)

    def test_plan_requires_clean_worktree_and_stays_planning_only(self):
        passport = consume(report(action="plan", status="READY", dirty=False))
        self.assertEqual(passport.status, "PLANNED_LOCAL")
        self.assertEqual(passport.dirty_digest, "")
        with self.assertRaisesRegex(DoctorEvidenceError, "clean"):
            consume(report(action="plan", status="READY", dirty=True))

    def test_process_exit_code_must_match_report(self):
        with self.assertRaisesRegex(DoctorEvidenceError, "process exit"):
            consume(report(), exit_code=2)

    def test_stale_sha_or_changed_scope_is_refused(self):
        stale = report()
        stale["exact_sha"] = "c" * 40
        with self.assertRaisesRegex(DoctorEvidenceError, "different SHA"):
            consume(stale)
        changed = report()
        changed["edit_contract"]["requested_paths"] = ["../outside"]
        with self.assertRaises(DoctorEvidenceError):
            consume(changed)

    def test_base_ref_worktree_and_planned_scope_are_bound(self):
        payload = report()
        payload["base_ref"] = "origin/main"
        with self.assertRaisesRegex(DoctorEvidenceError, "base ref"):
            consume(payload)

        payload = report()
        raw = json.dumps(payload).encode("utf-8")
        with self.assertRaisesRegex(DoctorEvidenceError, "worktree state"):
            consume_doctor_report(
                raw, observed_exit_code=0, expected_sha=SHA,
                expected_action="validate", expected_base_ref="HEAD",
                expected_paths=PATHS, expected_dirty=True,
                expected_dirty_digest="c" * 64, structural=False,
            )

        payload = report()
        payload["edit_contract"]["planned_paths"] = []
        with self.assertRaisesRegex(DoctorEvidenceError, "omits"):
            consume(payload)

    def test_structural_work_requires_passing_graphify(self):
        missing = report(structural=True)
        missing["selected_checks"]["graph_status"] = "MISSING"
        with self.assertRaisesRegex(DoctorEvidenceError, "Graphify"):
            consume(missing, structural=True)
        passport = consume(report(structural=True), structural=True)
        self.assertEqual((passport.graph_required, passport.graph_status),
                         (True, "PASS"))

    def test_local_report_cannot_claim_full_or_release_certification(self):
        for field in ("full_certified", "release_certified"):
            payload = report()
            payload["certification"][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(
                    DoctorEvidenceError, "certification claim"):
                consume(payload)

    def test_pass_requires_successful_execution_record(self):
        payload = report()
        payload["execution"]["status"] = "FAIL"
        with self.assertRaisesRegex(DoctorEvidenceError, "execution evidence"):
            consume(payload)

    def test_validate_pass_requires_scoped_certification_status(self):
        payload = report()
        payload["certification"]["status"] = "NOT_RUN"
        with self.assertRaisesRegex(DoctorEvidenceError, "certification status"):
            consume(payload)

    def test_untrusted_status_list_and_object_fail_closed(self):
        for invalid in (["PASS"], {"claim": "PASS"}):
            with self.subTest(invalid=invalid):
                payload = report()
                payload["status"] = invalid
                with self.assertRaisesRegex(DoctorEvidenceError, "status"):
                    consume(payload)

    def test_nonfinite_or_deep_json_does_not_crash_supervisor(self):
        arguments = dict(
            observed_exit_code=0, expected_sha=SHA,
            expected_action="validate", expected_base_ref="HEAD",
            expected_paths=PATHS, expected_dirty=True,
            expected_dirty_digest="b" * 64, structural=False,
        )
        nonfinite = json.dumps(report()).replace('"status": "PASS"', '"status": NaN')
        with self.assertRaises(DoctorEvidenceError):
            consume_doctor_report(nonfinite.encode(), **arguments)
        depth_attack = b"[" * 2500 + b"0" + b"]" * 2500
        with self.assertRaises(DoctorEvidenceError):
            consume_doctor_report(depth_attack, **arguments)
        self.assertEqual(consume(report()).status, "VALIDATED_LOCAL")

    def test_report_size_and_json_are_bounded(self):
        with self.assertRaises(DoctorEvidenceError):
            consume_doctor_report(
                b"x" * (MAX_REPORT_BYTES + 1), observed_exit_code=2,
                expected_sha=SHA, expected_action="validate",
                expected_base_ref="HEAD", expected_paths=PATHS,
                expected_dirty=True, expected_dirty_digest="b" * 64,
                structural=False,
            )

    def test_duplicate_keys_and_boolean_schema_or_exit_codes_are_rejected(self):
        raw = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(DoctorEvidenceError, "duplicate"):
            consume_doctor_report(
                raw, observed_exit_code=2, expected_sha=SHA,
                expected_action="validate", expected_base_ref="HEAD",
                expected_paths=PATHS, expected_dirty=True,
                expected_dirty_digest="b" * 64, structural=False,
            )
        for field in ("schema_version", "exit_code"):
            payload = report()
            payload[field] = True
            with self.subTest(field=field), self.assertRaises(DoctorEvidenceError):
                consume(payload)
        with self.assertRaisesRegex(DoctorEvidenceError, "valid JSON"):
            consume_doctor_report(
                b"not-json", observed_exit_code=2,
                expected_sha=SHA, expected_action="validate",
                expected_base_ref="HEAD", expected_paths=PATHS,
                expected_dirty=True, expected_dirty_digest="b" * 64,
                structural=False,
            )


if __name__ == "__main__":
    unittest.main()

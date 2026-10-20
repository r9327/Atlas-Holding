import json
import unittest

from holding.code_intelligence import (
    CodeIntelligenceError, SERENA_CORE_TOOLS, consume_semgrep_report,
    discover_serena, prepare_semgrep_scan,
)


RULES = "a" * 64


def consume_report(raw, *, exit_code):
    return consume_semgrep_report(
        raw, observed_exit_code=exit_code, expected_rules_sha256=RULES,
        observed_rules_sha256=RULES,
    )


class CodeIntelligenceTests(unittest.TestCase):
    def test_serena_absence_is_explicit(self):
        state = discover_serena(installed=False, version=None,
                                advertised_tools=(), permission="NONE")
        self.assertEqual((state.status, state.exposed_tools), ("UNAVAILABLE", ()))

    def test_serena_exposes_only_read_allowlist(self):
        advertised = tuple(SERENA_CORE_TOOLS) + ("replace_symbol_body", "execute_shell_command")
        state = discover_serena(installed=True, version="0.2.1",
                                advertised_tools=advertised, permission="READ")
        self.assertEqual(state.status, "READY")
        self.assertNotIn("replace_symbol_body", state.exposed_tools)
        self.assertNotIn("execute_shell_command", state.exposed_tools)

    def test_serena_missing_core_or_permission_is_blocked(self):
        state = discover_serena(installed=True, version="0.2.1",
                                advertised_tools=("find_symbol",), permission="READ")
        self.assertEqual(state.status, "BLOCKED")
        state = discover_serena(installed=True, version="0.2.1",
                                advertised_tools=tuple(SERENA_CORE_TOOLS), permission="NONE")
        self.assertEqual((state.status, state.exposed_tools), ("BLOCKED", ()))

    def test_serena_advertisement_is_typed_and_bounded(self):
        for tools in ("find_symbol", None, (str(index) for index in range(201))):
            with self.subTest(tools=tools), self.assertRaises(CodeIntelligenceError):
                discover_serena(
                    installed=True, version="0.2.1",
                    advertised_tools=tools, permission="READ",
                )

    def test_semgrep_request_is_local_bounded_and_offline(self):
        request = prepare_semgrep_scan(
            executable="semgrep", rules_path="rules/atlas.yml",
            rules_sha256="a" * 64, target_path="src", timeout_seconds=60,
        )
        self.assertIn("--metrics=off", request.argv)
        self.assertIn("--disable-version-check", request.argv)
        self.assertTrue(request.advisory_only)

    def test_semgrep_rejects_remote_or_traversing_config(self):
        for path in ("https://rules", "../rules.yml", "C:/rules.yml", "--rules.yml"):
            with self.subTest(path=path), self.assertRaises(CodeIntelligenceError):
                prepare_semgrep_scan(
                    executable="semgrep", rules_path=path,
                    rules_sha256="a" * 64, target_path="src", timeout_seconds=60,
                )
        with self.assertRaises(CodeIntelligenceError):
            prepare_semgrep_scan(
                executable="semgrep", rules_path="rules/atlas.yml",
                rules_sha256="a" * 64, target_path="--help", timeout_seconds=60,
            )

    def test_semgrep_finding_remains_advisory(self):
        raw = json.dumps({"results": [{
            "check_id": "atlas.rule", "path": "src/app.py", "start": {"line": 7},
            "extra": {"message": "unsafe call"},
        }], "errors": []}).encode()
        report = consume_report(raw, exit_code=1)
        self.assertEqual(report.status, "ADVISORY_FINDINGS")
        self.assertFalse(report.can_certify)
        self.assertFalse(report.can_promote_mission)

    def test_clean_semgrep_is_not_a_certification(self):
        raw = json.dumps({"results": [], "errors": []}).encode()
        report = consume_report(raw, exit_code=0)
        self.assertEqual(report.status, "ADVISORY_CLEAR")
        self.assertFalse(report.can_certify)

    def test_semgrep_errors_and_malformed_findings_fail_closed(self):
        raw = json.dumps({"results": [], "errors": [{"message": "timeout"}]}).encode()
        with self.assertRaisesRegex(CodeIntelligenceError, "scan errors"):
            consume_report(raw, exit_code=1)
        with self.assertRaisesRegex(CodeIntelligenceError, "execution failed"):
            consume_report(raw, exit_code=2)
        raw = json.dumps({"results": [{"path": "../secret"}], "errors": []}).encode()
        with self.assertRaisesRegex(CodeIntelligenceError, "shape"):
            consume_report(raw, exit_code=0)

    def test_semgrep_report_is_bound_to_observed_rules(self):
        raw = json.dumps({"results": [], "errors": []}).encode()
        with self.assertRaisesRegex(CodeIntelligenceError, "process evidence"):
            consume_semgrep_report(
                raw, observed_exit_code=0, expected_rules_sha256=RULES,
                observed_rules_sha256="b" * 64,
            )

    def test_semgrep_duplicate_json_keys_are_rejected(self):
        raw = b'{"results":[],"results":[],"errors":[]}'
        with self.assertRaisesRegex(CodeIntelligenceError, "duplicate JSON"):
            consume_report(raw, exit_code=0)


if __name__ == "__main__":
    unittest.main()

"""Regression checks for Holding's repository-level AI contribution contract.

These checks reuse the existing executable ToolRegistry policy and do not
self-attest GitHub certification, plugins or native Windows acceptance.
"""
from pathlib import Path
import unittest

from holding.tool_registry import validate_tool_registry


ROOT = Path(__file__).resolve().parents[1]


class ContributionContractTests(unittest.TestCase):
    def test_canonical_instructions_exist(self):
        required = (
            "AGENTS.md", "AI_ENTRYPOINT.md", "ZERO_TRUST_RULES.md",
            "PHASE_CERTIFICATION.md", ".github/copilot-instructions.md",
            "CLAUDE.md", ".cursor/rules/holding-quality.mdc",
            ".agents/skills/evidence-driven-validation/SKILL.md",
            ".agents/skills/plugin-safety/SKILL.md",
            ".agents/skills/root-cause-debugging/SKILL.md",
        )
        for path in required:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), path)

    def test_root_entrypoint_routes_to_real_authorities(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for name in ("AI_ENTRYPOINT.md", "ZERO_TRUST_RULES.md",
                     "PHASE_CERTIFICATION.md"):
            self.assertIn(name, text)

    def test_tool_registry_reuses_executable_policy(self):
        registry = validate_tool_registry(
            (ROOT / "config/tool_registry.json").read_bytes())
        self.assertFalse(registry.paid_services_allowed)
        self.assertFalse(registry.executable_hub_ready)
        owner = dict(registry.capability_owner)
        self.assertEqual(owner["quality.validate"], "doctor")
        self.assertEqual(owner["docs.lookup"], "context7")
        self.assertEqual(owner["web.search"], "firecrawl")
        for tool in registry.tools:
            if tool.network:
                self.assertFalse(tool.required)

    def test_certification_requires_exact_sha_and_both_platforms(self):
        source = (ROOT / ".github/workflows/holding-certification.yml").read_text(
            encoding="utf-8")
        for fragment in (
            "ready_for_review", "synchronize", "github.event.pull_request.draft",
            "github.event.pull_request.head.sha", "git rev-parse HEAD",
            "ubuntu-latest", "windows-latest", "test_*.py",
            "tools.architecture_guard", "Holding Certification / Required",
            'needs.full-suite.result',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

    def test_policy_does_not_claim_release_certification(self):
        text = (ROOT / "PHASE_CERTIFICATION.md").read_text(encoding="utf-8")
        self.assertIn("RELEASE_CERTIFIED", text)
        self.assertIn("docs/QUALITY_RELEASE_GATES.md", text)
        self.assertIn("rulesets GitHub", text)


if __name__ == "__main__":
    unittest.main()

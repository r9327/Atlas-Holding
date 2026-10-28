"""Non-destructive contract regression tests for AI governance."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ai_governance_guard", ROOT / "tools/ai_governance_guard.py")
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GovernanceContractTests(unittest.TestCase):
    def test_repository_contract(self):
        self.assertEqual(guard.check(ROOT), [])

    def test_missing_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in guard.DOCUMENTS:
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, dest)
            (root / "ZERO_TRUST_RULES.md").unlink()
            self.assertIn("missing: ZERO_TRUST_RULES.md", guard.check(root))

    def test_registry_is_validated_by_existing_canonical_validator(self):
        from holding.tool_registry import validate_tool_registry
        registry = validate_tool_registry((ROOT / "config/tool_registry.json").read_bytes())
        self.assertEqual(dict(registry.capability_owner)["quality.validate"], "doctor")
        self.assertFalse(registry.paid_services_allowed)
        self.assertFalse(registry.executable_hub_ready)

    def test_workflow_does_not_claim_full_certification(self):
        workflow = (ROOT / ".github/workflows/ai_governance_validation.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("github.event.pull_request.head.sha", workflow)
        self.assertIn("ai_governance_guard.py", workflow)
        self.assertNotIn("CERTIFIED=true", workflow)


if __name__ == "__main__":
    unittest.main()

"""Tests of Holding AI policy coherence and fail-closed behavior."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tools.ai_policy_check import REQUIRED, ROOT, verify


class AiPolicyCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for path in REQUIRED:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)

    def test_valid_repository(self) -> None:
        self.assertEqual(verify(self.root), [])

    def test_missing_contract_blocks(self) -> None:
        (self.root / "ZERO_TRUST_RULES.md").unlink()
        self.assertIn("missing or empty contract: ZERO_TRUST_RULES.md", verify(self.root))

    def test_unqualified_model_name_blocks(self) -> None:
        path = self.root / ".ai/task_profiles.json"
        profiles = json.loads(path.read_text(encoding="utf-8"))
        profiles["task_profiles"]["certification"]["recommended_profile"] = "UNKNOWN"
        path.write_text(json.dumps(profiles), encoding="utf-8")
        self.assertIn("invalid task: certification", verify(self.root))

    def test_executable_routing_claim_blocks(self) -> None:
        path = self.root / ".ai/task_profiles.json"
        profiles = json.loads(path.read_text(encoding="utf-8"))
        profiles["status"] = "fully_automated"
        path.write_text(json.dumps(profiles), encoding="utf-8")
        self.assertTrue(any("cannot claim" in error for error in verify(self.root)))

    def test_paid_registry_blocks(self) -> None:
        path = self.root / "config/tool_registry.json"
        registry = json.loads(path.read_text(encoding="utf-8"))
        registry["allow_paid_services"] = True
        path.write_text(json.dumps(registry), encoding="utf-8")
        self.assertTrue(any("invalid tool registry" in error for error in verify(self.root)))

    def test_missing_provider_pointer_blocks(self) -> None:
        (self.root / "CLAUDE.md").write_text("# unrelated\n", encoding="utf-8")
        self.assertIn("missing agent entrypoint pointer: CLAUDE.md", verify(self.root))


if __name__ == "__main__":
    unittest.main()

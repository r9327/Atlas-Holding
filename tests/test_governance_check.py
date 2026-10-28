"""Deterministic governance checks, including rejected unsafe changes."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.governance_check import CONTRACTS, GovernanceError, ROOT, inspect


class GovernanceCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for filename in CONTRACTS:
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("canonical\n", encoding="utf-8")
        reg = self.root / "config" / "tool_registry.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_bytes((ROOT / "config" / "tool_registry.json").read_bytes())
        self.registry_path = reg

    def test_current_contracts_are_accepted(self):
        result = inspect(ROOT)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["phase_certified"])
        self.assertFalse(result["plugins_installed_verified"])

    def test_fixture_passes_static_policy_only(self):
        result = inspect(self.root)
        self.assertEqual(result["scope"], "STATIC_POLICY_ONLY")
        self.assertIsNone(result["candidate_sha"])

    def test_missing_contract_blocks(self):
        (self.root / "AI_ENTRYPOINT.md").unlink()
        with self.assertRaisesRegex(GovernanceError, "Missing canonical"):
            inspect(self.root)

    def test_paid_services_block(self):
        payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        payload["allow_paid_services"] = True
        self.registry_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(GovernanceError, "Invalid tool registry"):
            inspect(self.root)

    def test_core_capability_ownership_cannot_change_silently(self):
        payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        doctor = next(x for x in payload["tools"] if x["id"] == "doctor")
        doctor["capabilities"].remove("quality.validate")
        self.registry_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(GovernanceError, "core capabilities"):
            inspect(self.root)

    def test_wrong_sha_blocks(self):
        with self.assertRaisesRegex(GovernanceError, "Candidate SHA mismatch"):
            inspect(ROOT, "0" * 40)


if __name__ == "__main__":
    unittest.main()

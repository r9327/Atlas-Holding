"""Non-regression tests for the declarative AI/tool/plugin contract checker."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.ai_contract_check import ROOT, audit


class AiContractTests(unittest.TestCase):
    def test_repository_policy_is_consistent(self):
        self.assertEqual([], audit(ROOT))

    def test_missing_authority_files_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(any("Missing governance contract" in issue for issue in audit(Path(folder))))

    def test_duplicate_tool_is_rejected(self):
        config = json.loads((ROOT / "config/tool_registry.json").read_text(encoding="utf-8"))
        config["tools"].append(copy.deepcopy(config["tools"][0]))
        self.assertIn("Duplicate tool id: ai_context", self._registry_errors(config))

    def test_paid_default_is_rejected(self):
        config = json.loads((ROOT / "config/tool_registry.json").read_text(encoding="utf-8"))
        config["allow_paid_services"] = True
        self.assertTrue(any("Paid services" in issue for issue in self._registry_errors(config)))

    def test_uninstalled_remote_plugin_cannot_be_required(self):
        config = json.loads((ROOT / "config/tool_registry.json").read_text(encoding="utf-8"))
        next(tool for tool in config["tools"] if tool["id"] == "context7")["required"] = True
        self.assertTrue(any("Uninstalled network tool" in issue for issue in self._registry_errors(config)))

    def _registry_errors(self, config: dict) -> list[str]:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config"
            path.mkdir()
            (path / "tool_registry.json").write_text(json.dumps(config), encoding="utf-8")
            return audit(Path(folder))


if __name__ == "__main__":
    unittest.main()

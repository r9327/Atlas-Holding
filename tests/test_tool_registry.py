import json
from pathlib import Path
import unittest

from holding.tool_registry import ToolRegistryError, validate_tool_registry


ROOT = Path(__file__).resolve().parents[1]


def registry_dict():
    return json.loads((ROOT / "config" / "tool_registry.json").read_text(encoding="utf-8"))


def validate(value):
    return validate_tool_registry(json.dumps(value).encode("utf-8"))


class ToolRegistryTests(unittest.TestCase):
    def test_repository_registry_satisfies_executable_policy(self):
        registry = validate_tool_registry(
            (ROOT / "config" / "tool_registry.json").read_bytes())
        self.assertFalse(registry.paid_services_allowed)
        self.assertFalse(registry.executable_hub_ready)
        self.assertIn(("quality.validate", "doctor"), registry.capability_owner)

    def test_paid_or_online_default_is_rejected(self):
        for field, value in (("allow_paid_services", True), ("default_network", "online")):
            payload = registry_dict(); payload[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                    ToolRegistryError, "zero-cost"):
                validate(payload)

    def test_network_tool_cannot_be_required_offline(self):
        payload = registry_dict(); payload["tools"][6]["required"] = True
        with self.assertRaisesRegex(ToolRegistryError, "mandatory offline"):
            validate(payload)

    def test_network_automation_requires_known_guard(self):
        payload = registry_dict(); payload["tools"][6]["automatic"] = True
        with self.assertRaisesRegex(ToolRegistryError, "explicit guard"):
            validate(payload)

    def test_mutating_tool_cannot_be_unconditionally_automatic(self):
        payload = registry_dict(); payload["tools"][4]["automatic"] = True
        with self.assertRaisesRegex(ToolRegistryError, "Mutation"):
            validate(payload)

    def test_unknown_automation_or_untyped_option_is_rejected(self):
        payload = registry_dict(); payload["tools"][0]["automatic"] = "trust-me"
        with self.assertRaisesRegex(ToolRegistryError, "Unknown local"):
            validate(payload)
        payload = registry_dict(); payload["tools"][0]["prefer_local"] = "yes"
        with self.assertRaisesRegex(ToolRegistryError, "prefer_local"):
            validate(payload)

    def test_registry_cannot_claim_installation_or_hub_implementation(self):
        payload = registry_dict(); payload["tools"][6]["installed_locally"] = True
        with self.assertRaisesRegex(ToolRegistryError, "self-certify"):
            validate(payload)
        payload = registry_dict(); payload["local_mcp_hub"]["implemented"] = True
        with self.assertRaisesRegex(ToolRegistryError, "Unproved"):
            validate(payload)

    def test_duplicate_ids_or_capability_owners_are_rejected(self):
        payload = registry_dict(); payload["tools"][1]["id"] = payload["tools"][0]["id"]
        with self.assertRaisesRegex(ToolRegistryError, "duplicate tool"):
            validate(payload)
        payload = registry_dict()
        payload["tools"][1]["capabilities"][0] = payload["tools"][0]["capabilities"][0]
        with self.assertRaisesRegex(ToolRegistryError, "multiple declared owners"):
            validate(payload)

    def test_boolean_does_not_pass_as_schema_or_concurrency_integer(self):
        for field in ("schema_version", "max_concurrent_heavy_models"):
            payload = registry_dict(); payload[field] = True
            with self.subTest(field=field), self.assertRaises(ToolRegistryError):
                validate(payload)


if __name__ == "__main__":
    unittest.main()

"""Offline integrity inventory must never execute or enumerate unknown plugins."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from holding.mcp_entry import router
from holding.mcp_local_inventory import (
    MAX_PLUGINS, PinnedLocalPlugin, inspect_pinned_plugin,
    inspect_pinned_plugins,
)
from holding.mcp_stdio import PROTOCOL_VERSION


def digest(data):
    return hashlib.sha256(data).hexdigest()


def invoke(server, tool):
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            "name": tool, "arguments": {},
        },
    }
    raw = json.dumps(request).encode("utf-8") + b"\n"
    return json.loads(server.handle_line(raw))


class PinnedLocalPluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "test.plugin"
        self.file.write_bytes(b"verified plugin executable file")
        self.pin = PinnedLocalPlugin(
            "test-local", "1.0", self.file,
            digest(self.file.read_bytes()),
        )

    def test_existing_exact_file_is_verified_without_running_it(self):
        result = inspect_pinned_plugin(self.pin)
        self.assertEqual(result.name, "test-local")
        self.assertEqual(result.binary_state, "BINARY_VERIFIED")
        self.assertEqual(result.observed_sha256, self.pin.expected_sha256)
        self.assertFalse(hasattr(result, "is_running"))
        self.assertFalse(hasattr(result, "permissions"))

    def test_modified_missing_directory_and_symlink_are_not_verified(self):
        self.file.write_bytes(b"replaced binary payload")
        modified = inspect_pinned_plugin(self.pin)
        self.assertEqual(modified.binary_state, "HASH_MISMATCH")
        self.assertIsNone(modified.observed_sha256)
        self.file.unlink()
        self.assertEqual(inspect_pinned_plugin(self.pin).binary_state, "MISSING")
        self.file.mkdir()
        self.assertEqual(
            inspect_pinned_plugin(self.pin).binary_state, "NOT_REGULAR_FILE")
        self.file.rmdir()
        try:
            self.file.symlink_to(Path(self.tmp.name) / "missing-target")
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable for this Windows account")
        self.assertEqual(
            inspect_pinned_plugin(self.pin).binary_state, "NOT_REGULAR_FILE")

    def test_malformed_or_untrusted_pins_are_refused(self):
        for parameters in (
            {"name": "../escape"},
            {"name": "not a key"},
            {"declared_version": "1 2"},
            {"expected_sha256": "c" * 40},
            {"executable": Path("relative.exe")},
        ):
            with self.subTest(parameters=parameters):
                kwargs = dict(
                    name=self.pin.name,
                    declared_version=self.pin.declared_version,
                    executable=self.pin.executable,
                    expected_sha256=self.pin.expected_sha256,
                )
                kwargs.update(parameters)
                with self.assertRaises(ValueError):
                    PinnedLocalPlugin(**kwargs)
        with self.assertRaises(ValueError):
            inspect_pinned_plugin(self.file)

    def test_inventory_is_bounded_deduplicated_and_ordered(self):
        more = PinnedLocalPlugin(
            "aaa", self.pin.declared_version,
            self.pin.executable, self.pin.expected_sha256,
        )
        ordered = inspect_pinned_plugins((self.pin, more))
        self.assertEqual([x.name for x in ordered], ["aaa", "test-local"])
        with self.assertRaises(ValueError):
            inspect_pinned_plugins((self.pin, self.pin))
        with self.assertRaises(ValueError):
            inspect_pinned_plugins(tuple(self.pin for _ in range(MAX_PLUGINS + 1)))
        with self.assertRaises(ValueError):
            inspect_pinned_plugins([self.pin])

    def test_host_explicitly_enables_readonly_mcp_binary_inventory(self):
        default = router()
        self.assertEqual([name for name in default._tools],
                         ["holding.health"])
        configured = router(installed_plugins=(self.pin,))
        result = invoke(configured, "holding.local_plugin_inventory")["result"]
        self.assertFalse(result["isError"])
        content = json.loads(result["content"][0]["text"])
        self.assertEqual(len(content["plugins"]), 1)
        self.assertEqual(
            content["plugins"][0]["binary_state"], "BINARY_VERIFIED")
        self.assertEqual(content["source"], "TRUSTED_LOCAL_PIN_SHA256")
        self.assertFalse(content["executes_plugins"])
        self.assertFalse(content["proves_runtime_available"])
        self.assertNotIn(str(self.file), json.dumps(content))
        # Replacing a file invalidates the next reading; no cached ACTIVE flag.
        self.file.write_bytes(b"changed")
        next_run = invoke(configured, "holding.local_plugin_inventory")
        report = json.loads(next_run["result"]["content"][0]["text"])
        self.assertEqual(report["plugins"][0]["binary_state"], "HASH_MISMATCH")
        self.assertIsNone(report["plugins"][0]["observed_sha256"])

    def test_inventory_can_only_be_supplied_by_trusted_pin_collection(self):
        for invalid in ((self.file,), [self.pin], tuple(self.pin for _ in range(33))):
            with self.subTest(len=len(invalid)), self.assertRaises(ValueError):
                router(installed_plugins=invalid)


if __name__ == "__main__":
    unittest.main()

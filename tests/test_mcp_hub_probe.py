"""Real platform-independent internal MCP hub protocol smoke contracts."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from holding.mcp_hub_probe import (
    MAX_PROBE_BYTES, _discover_packet, _valid_response,
    probe_local_hub,
)
from holding.mcp_local_inventory import PinnedLocalPlugin
from holding.mcp_stdio import PROTOCOL_VERSION


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable).resolve()


def pinned(digest=None):
    h = hashlib.sha256()
    with PYTHON.open("rb") as f:
        while chunk := f.read(64 * 1024):
            h.update(chunk)
    return PinnedLocalPlugin(
        "hosting-python", "3.13", PYTHON, digest or h.hexdigest()
    )


class LocalHubProbeTests(unittest.TestCase):
    def test_real_own_subprocess_answers_mcp_discovery(self):
        result = probe_local_hub(
            interpreter_pin=pinned(), repository_root=ROOT,
            timeout_seconds=8,
        )
        self.assertEqual(result.status, "PROTOCOL_RESPONSIVE")
        self.assertEqual(result.protocol_version, PROTOCOL_VERSION)
        self.assertEqual(result.server_name, "atlas-holding-local")
        self.assertFalse(result.external_plugins_tested)

    def test_failed_binary_hash_is_not_executed(self):
        bogus = pinned("0" * 64)
        with patch("holding.mcp_hub_probe.subprocess.Popen") as popen:
            state = probe_local_hub(
                interpreter_pin=bogus, repository_root=ROOT,
            )
            self.assertEqual(state.status, "BINARY_UNVERIFIED")
            popen.assert_not_called()

    def test_arbitrary_python_or_relative_root_cannot_be_launched(self):
        with self.assertRaises(ValueError):
            probe_local_hub(
                interpreter_pin=pinned(),
                repository_root=Path("."),
            )
        with self.assertRaises(ValueError):
            probe_local_hub(
                interpreter_pin=pinned(),
                repository_root=ROOT,
                timeout_seconds=100,
            )
        with self.assertRaises(ValueError):
            probe_local_hub(
                interpreter_pin=pinned(),
                repository_root=ROOT,
                expected_server="other-local-server",
            )

    def test_forged_local_package_path_is_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_root = Path(directory)
            malicious = fake_root / "holding"
            malicious.mkdir()
            (malicious / "mcp_entry.py").write_text(
                "raise RuntimeError('this module must never execute')",
                encoding="utf-8",
            )
            with patch("holding.mcp_hub_probe.subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "own installed package"):
                    probe_local_hub(
                        interpreter_pin=pinned(), repository_root=fake_root,
                    )
                launch.assert_not_called()

    def test_discovery_response_requires_exact_version_server_and_shape(self):
        good = {
            "jsonrpc": "2.0", "id": "holding-probe-1",
            "result": {
                "resultType": "complete",
                "supportedVersions": [PROTOCOL_VERSION],
                "capabilities": {"tools": {}},
                "_meta": {"io.modelcontextprotocol/serverInfo": {
                    "name": "atlas-holding-local", "version": "0.1.0",
                }},
            },
        }
        encoded = (json.dumps(good) + "\n").encode("ascii")
        self.assertTrue(_valid_response(encoded, "atlas-holding-local"))
        self.assertFalse(_valid_response(encoded + encoded, "atlas-holding-local"))
        self.assertFalse(_valid_response(b"x" * (MAX_PROBE_BYTES + 1),
                                         "atlas-holding-local"))
        self.assertFalse(_valid_response(b"{\"id\":1,\"id\":2}\n",
                                         "atlas-holding-local"))
        for corrupted in (
            {"id": "other-request"},
            {"jsonrpc": "1.0"},
            {"result": {"resultType": "complete"}},
            {"result": dict(good["result"], supportedVersions=["2099-01-01"])},
            {"result": dict(good["result"], capabilities={"network": True})},
            {"result": dict(good["result"], _meta={
                "io.modelcontextprotocol/serverInfo": {
                    "name": "untrusted-plugin", "version": "1.0",
                },
            })},
        ):
            invalid = dict(good, **corrupted)
            self.assertFalse(_valid_response(
                (json.dumps(invalid) + "\n").encode(),
                "atlas-holding-local",
            ))

    def test_bounded_probe_does_not_inherit_secrets_or_execute_plugin_args(self):
        # A test double captures the exact command and environment without
        # executing anything. One fixed internal module, not arbitrary argv.
        response = {
            "jsonrpc": "2.0", "id": "holding-probe-1",
            "result": {
                "resultType": "complete",
                "supportedVersions": [PROTOCOL_VERSION],
                "capabilities": {"tools": {}},
                "_meta": {"io.modelcontextprotocol/serverInfo": {
                    "name": "atlas-holding-local", "version": "0.1",
                }},
            },
        }
        class FakeProcess:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO((json.dumps(response) + "\n").encode())
                self.returncode = 0
            def wait(self, timeout=None):
                return 0
            def poll(self):
                return 0
            def kill(self):
                raise AssertionError("Completed process must not be killed")

        with patch.dict("os.environ", {
                "OPENAI_API_KEY": "DO_NOT_PASS",
                "PRIVATE_ACCESS_TOKEN": "SECRET_DO_NOT_PASS",
            }):
            with patch("holding.mcp_hub_probe.subprocess.Popen",
                       return_value=FakeProcess()) as popen:
                result = probe_local_hub(
                    interpreter_pin=pinned(), repository_root=ROOT)
                self.assertEqual(result.status, "PROTOCOL_RESPONSIVE")
                kwargs = popen.call_args.kwargs
                self.assertEqual(
                    popen.call_args.args[0],
                    [str(PYTHON), "-B", "-m", "holding.mcp_entry"],
                )
                self.assertFalse(kwargs["shell"])
                self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
                self.assertNotIn("PRIVATE_ACCESS_TOKEN", kwargs["env"])
                self.assertEqual(kwargs["cwd"], str(ROOT))

    def test_oversized_stdout_never_becomes_a_success(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(b"x" * (MAX_PROBE_BYTES + 5))
                self.returncode = 0
            def wait(self, timeout=None):
                return 0
            def poll(self):
                return 0
            def kill(self):
                raise AssertionError("Fake process is already completed")

        with patch("holding.mcp_hub_probe.subprocess.Popen",
                   return_value=FakeProcess()):
            report = probe_local_hub(
                interpreter_pin=pinned(), repository_root=ROOT)
            self.assertEqual(report.status, "OUTPUT_LIMIT")
            self.assertIsNone(report.server_name)


if __name__ == "__main__":
    unittest.main()

"""Integration contracts for trusted MCP bootstrap and real stdio process."""
import io
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from holding.mcp_bootstrap import McpBootstrapError, load_bootstrap_manifest
from holding.mcp_cache import McpCacheError, OfflineDocumentCache
from holding.mcp_entry import main
from holding.mcp_stdio import PROTOCOL_VERSION


NOW = 1_800_000_000
URI = "https://example.org/dofus/guide"
VER = "1.2"


def packet(method, *, name=None, arguments=None):
    params = {"_meta": {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }}
    if name is not None:
        params.update(name=name, arguments={} if arguments is None else arguments)
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode("utf-8") + b"\n"


class ProvisionedMcpBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache_path = self.root / "offline.sqlite"
        self.binary = self.root / "tool.exe"
        self.binary.write_bytes(b"This file is NOT executable code")
        self.binary_hash = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.manifest = self.root / "mcp-bootstrap.json"
        with OfflineDocumentCache(self.cache_path) as writable:
            raw = b"Source text only, never trusted commands"
            writable.put(
                source_uri=URI, source_version=VER,
                captured_epoch=NOW - 50, expires_epoch=NOW + 50,
                now_epoch=NOW, content=raw,
                content_sha256=hashlib.sha256(raw).hexdigest(),
            )
        self.data = {
            "schema_version": 1, "cache_database": str(self.cache_path),
            "pins": [{
                "name": "doctor-local",
                "declared_version": "1.0",
                "executable": str(self.binary),
                "expected_sha256": self.binary_hash,
            }],
        }
        self.write_manifest()
        if os.name == "nt":
            # The default runner Temp DACL is not evidence of a private
            # supervisor installation. Provision an explicit trusted ACL.
            from holding.mcp_windows_acl import current_windows_user_sid
            sid = current_windows_user_sid()
            for target, rights in (
                    (self.root, "(OI)(CI)F"),
                    (self.cache_path, "F"),
                    (self.manifest, "F")):
                for command in (
                        ["icacls", str(target), "/inheritance:r"],
                        ["icacls", str(target), "/grant:r",
                         f"*{sid}:{rights}", f"*S-1-5-18:{rights}",
                         f"*S-1-5-32-544:{rights}"]):
                    subprocess.run(
                        command, check=True, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=10)

    def write_manifest(self, value=None):
        self.manifest.write_text(
            json.dumps(self.data if value is None else value),
            encoding="utf-8",
        )
        if os.name == "posix":
            self.manifest.chmod(0o600)

    def run_stdio(self, *, request=None, args=None):
        out, err = io.BytesIO(), io.StringIO()
        code = main(
            [] if args is None else args,
            stdin=io.BytesIO(b"" if request is None else request),
            stdout=out, stderr=err,
        )
        return code, out.getvalue(), err.getvalue()

    def test_managed_startup_registers_readonly_tools_and_not_executors(self):
        config = load_bootstrap_manifest(self.manifest)
        self.assertEqual(len(config.pins), 1)
        self.assertEqual(config.cache_database, self.cache_path)
        args = ["--bootstrap-manifest", str(self.manifest)]
        code, raw, errors = self.run_stdio(
            args=args, request=packet("tools/list"))
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        tools = json.loads(raw)["result"]["tools"]
        self.assertEqual([x["name"] for x in tools], [
            "holding.cache_lookup", "holding.health",
            "holding.local_plugin_inventory",
        ])
        self.assertFalse(self.binary.read_bytes().startswith(b"#!"))

    def test_readonly_cache_cannot_be_changed_by_process_or_writer_api(self):
        with OfflineDocumentCache(self.cache_path, readonly=True) as cache:
            self.assertTrue(cache.readonly)
            self.assertEqual(cache.lookup(
                source_uri=URI, source_version=VER,
                now_epoch=NOW).status, "CACHED_ONLY")
            with self.assertRaisesRegex(McpCacheError, "Read-only"):
                cache.put(
                    source_uri=URI, source_version=VER,
                    captured_epoch=NOW, expires_epoch=NOW + 10,
                    now_epoch=NOW, content=b"overwrite",
                    content_sha256="f" * 64,
                )
            with self.assertRaises(sqlite3.OperationalError):
                cache.db.execute("DELETE FROM mcp_cached_docs")
        self.assertFalse((self.root / "new.sqlite").exists())
        with self.assertRaises(McpCacheError):
            OfflineDocumentCache(self.root / "new.sqlite", readonly=True)

    def test_subprocess_boots_real_mcp_stdio_without_exec_or_network(self):
        process = subprocess.run(
            [sys.executable, "-B", "-m", "holding.mcp_entry",
             "--bootstrap-manifest", str(self.manifest)],
            input=packet("tools/call", name="holding.local_plugin_inventory"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=Path(__file__).resolve().parents[1],
            timeout=15,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr[:500])
        self.assertEqual(process.stderr, b"")
        response = json.loads(process.stdout)
        data = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(data["plugins"][0]["binary_state"], "BINARY_VERIFIED")
        self.assertFalse(data["executes_plugins"])
        self.assertFalse(data["proves_runtime_available"])
        self.assertNotIn(str(self.binary), process.stdout.decode("utf-8"))

    def test_no_manifest_starts_health_only_and_refuses_cache_tool(self):
        code, raw, _err = self.run_stdio(request=packet("tools/list"))
        self.assertEqual(code, 0)
        names = [x["name"] for x in json.loads(raw)["result"]["tools"]]
        self.assertEqual(names, ["holding.health"])
        code, raw, _err = self.run_stdio(request=packet(
            "tools/call", name="holding.cache_lookup",
            arguments={"source_uri": URI, "source_version": VER}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(raw)["error"]["code"], -32602)

    def test_malformed_manifest_aborts_without_any_mcp_stdout(self):
        wrong = [
            {"schema_version": True, "cache_database": None, "pins": []},
            {"schema_version": 99, "cache_database": None, "pins": []},
            {"schema_version": 1, "cache_database": "", "pins": []},
            {"schema_version": 1, "cache_database": "relative.sqlite", "pins": []},
            {"schema_version": 1, "cache_database": None,
             "pins": [{"name": "fake"}]},
            {"schema_version": 1, "cache_database": None, "pins": [],
             "token": "SHOULD-NOT-LOG"},
            {"schema_version": 1, "cache_database": None, "pins": None},
            {"schema_version": 1, "cache_database": None,
             "pins": [self.data["pins"][0]] * 2},
            {"schema_version": 1, "cache_database": None,
             "pins": [{"name": "evil", "declared_version": "1",
                       "executable": "relative.exe",
                       "expected_sha256": self.binary_hash}]},
        ]
        for value in wrong:
            with self.subTest(value=value):
                self.write_manifest(value)
                code, output, errors = self.run_stdio(
                    args=["--bootstrap-manifest", str(self.manifest)],
                    request=packet("tools/list"))
                self.assertEqual(code, 2)
                self.assertEqual(output, b"")
                self.assertIn("rejected", errors)
                self.assertNotIn("SHOULD-NOT-LOG", errors)

    def test_duplicate_keys_or_unsafe_manifest_path_fail_closed(self):
        self.manifest.write_text(
            '{"schema_version":1,"schema_version":1,'
            '"cache_database":null,"pins":[]}',
            encoding="utf-8",
        )
        code, raw, _err = self.run_stdio(
            args=["--bootstrap-manifest", str(self.manifest)],
            request=packet("tools/list"))
        self.assertEqual(code, 2)
        self.assertEqual(raw, b"")
        self.write_manifest()
        code, raw, err = self.run_stdio(
            args=["--bootstrap-manifest", str(self.root / "absent.json")],
            request=packet("tools/list"))
        self.assertEqual(code, 2)
        self.assertEqual(raw, b"")
        self.assertNotIn(str(self.root), err)

    @unittest.skipUnless(os.name == "posix", "POSIX private manifest gate")
    def test_world_writable_manifest_is_refused_without_side_effects(self):
        self.manifest.chmod(0o666)
        code, raw, _err = self.run_stdio(
            args=["--bootstrap-manifest", str(self.manifest)],
            request=packet("tools/list"))
        self.assertEqual(code, 2)
        self.assertEqual(raw, b"")

    def test_broken_cache_does_not_downgrade_to_health_only_silently(self):
        missing = dict(self.data, cache_database=str(self.root / "no-cache.sqlite"))
        self.write_manifest(missing)
        code, raw, stderr = self.run_stdio(
            args=["--bootstrap-manifest", str(self.manifest)],
            request=packet("tools/list"),
        )
        self.assertEqual(code, 2)
        self.assertEqual(raw, b"")
        self.assertIn("rejected", stderr)
        self.assertFalse((self.root / "no-cache.sqlite").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX parent directory gate")
    def test_world_writable_manifest_parent_is_denied(self):
        self.root.chmod(0o777)
        try:
            code, raw, stderr = self.run_stdio(
                args=["--bootstrap-manifest", str(self.manifest)],
                request=packet("tools/list"),
            )
            self.assertEqual(code, 2)
            self.assertEqual(raw, b"")
            self.assertIn("rejected", stderr)
        finally:
            self.root.chmod(0o700)

    def test_manifest_symlink_is_rejected_without_running_any_plugin(self):
        link = self.root / "redirected.json"
        try:
            link.symlink_to(self.manifest)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink permission not available")
        code, raw, stderr = self.run_stdio(
            args=["--bootstrap-manifest", str(link)],
            request=packet("tools/list"),
        )
        self.assertEqual(code, 2)
        self.assertEqual(raw, b"")
        self.assertIn("rejected", stderr)

    def test_real_subprocess_cache_tool_returns_stale_not_false_live_data(self):
        process = subprocess.run(
            [sys.executable, "-B", "-m", "holding.mcp_entry",
             "--bootstrap-manifest", str(self.manifest)],
            input=packet(
                "tools/call", name="holding.cache_lookup",
                arguments={"source_uri": URI, "source_version": VER},
            ),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=Path(__file__).resolve().parents[1],
            timeout=15, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr[:500])
        entry = json.loads(json.loads(process.stdout)["result"]["content"][0]["text"])
        self.assertTrue(entry["offline"])
        self.assertFalse(entry["has_live_network"])
        self.assertEqual(entry["provenance"], "UNTRUSTED_EXTERNAL")
        self.assertIn(entry["status"], {"STALE", "CACHED_ONLY"})
        if entry["status"] == "STALE":
            self.assertIsNone(entry["text"])

    def test_rejects_unknown_startup_args_without_echoing_secrets(self):
        code, stdout, stderr = self.run_stdio(
            args=["--token", "SUPER-SECRET"],
            request=packet("tools/list"))
        self.assertEqual(code, 2)
        self.assertEqual(stdout, b"")
        self.assertNotIn("SUPER-SECRET", stderr)


if __name__ == "__main__":
    unittest.main()

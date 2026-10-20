"""Read-only installer preflight: deterministic classification and local API limits."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from holding import inventory as inv


class InventoryTests(unittest.TestCase):
    def test_timed_out_probe_cannot_leave_a_descendant_running(self):
        with tempfile.TemporaryDirectory() as root:
            heartbeat = Path(root) / "descendant-heartbeat"
            descendant = (
                "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                "exec(\"while True:\\n p.open('a').write('x')\\n time.sleep(.03)\")"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
                "time.sleep(60)"
            )
            result = inv._command(
                sys.executable,
                ("-c", parent, descendant, str(heartbeat)),
                timeout=2,
            )
            self.assertEqual(result, (-1, ""))
            self.assertTrue(heartbeat.is_file(), "descendant never started")
            size_after_timeout = heartbeat.stat().st_size
            self.assertGreater(size_after_timeout, 0)
            time.sleep(.25)
            self.assertEqual(heartbeat.stat().st_size, size_after_timeout)

    def test_program_probe_does_not_inherit_session_secrets(self):
        before = os.environ.get("GITHUB_TOKEN")
        os.environ["GITHUB_TOKEN"] = "must-not-reach-probe"
        try:
            code, output = inv._command(
                sys.executable,
                ("-c", "import os; print(os.getenv('GITHUB_TOKEN'))"),
            )
        finally:
            if before is None:
                os.environ.pop("GITHUB_TOKEN", None)
            else:
                os.environ["GITHUB_TOKEN"] = before
        self.assertEqual((code, output), (0, "None"))

    def test_program_not_found_is_never_called(self):
        with patch.object(inv.shutil, "which", return_value=None):
            with patch.object(inv, "_command") as run:
                found = inv._program("opencode")
        self.assertEqual(found.status, "NOT_FOUND")
        run.assert_not_called()

    def test_program_healthcheck_failure_is_not_claimed_ready(self):
        with patch.object(inv.shutil, "which", return_value="/usr/bin/git"):
            with patch.object(inv, "_command", return_value=(1, "error")):
                found = inv._program("git")
        self.assertEqual(found.status, "FOUND_BROKEN")

    def test_ollama_healthy_lists_real_local_models(self):
        exe = inv.Component("FOUND_VALID", path="/usr/bin/ollama", version="0.1")
        observed = []
        def endpoint(route):
            observed.append(route)
            if route == "/api/version":
                return {"version": "0.12.0"}
            return {"models": [{"name": "coder:test", "digest": "sha256:abc",
                                "size": 1000, "details": {"family": "qwen",
                                "quantization_level": "Q4_K_M"}},
                               {"name": "incomplete", "size": 100}]}
        with patch.object(inv, "_api_json", side_effect=endpoint):
            status, models = inv._ollama(exe)
        self.assertEqual(observed, ["/api/version", "/api/tags"])
        self.assertEqual(status.status, "FOUND_VALID")
        self.assertEqual(len(models), 1)
        self.assertEqual((models[0].name, models[0].quantization),
                         ("coder:test", "Q4_K_M"))

    def test_stopped_ollama_preserves_installed_version_but_not_readiness(self):
        installed = inv.Component("FOUND_VALID", path="C:/ollama.exe", version="ollama 0.12")
        with patch.object(inv, "_api_json", side_effect=OSError("connection refused")):
            status, models = inv._ollama(installed)
        self.assertEqual(status.status, "FOUND_BROKEN")
        self.assertEqual(status.version, installed.version)
        self.assertEqual(models, ())

    def test_uninstalled_ollama_does_not_contact_service(self):
        with patch.object(inv, "_api_json") as api:
            status, models = inv._ollama(inv.Component("NOT_FOUND"))
        api.assert_not_called()
        self.assertEqual((status.status, models), ("NOT_FOUND", ()))

    def test_gpu_memory_is_measured_not_guessed(self):
        with patch.object(inv.shutil, "which", return_value="nvidia-smi"):
            with patch.object(inv, "_command", return_value=(0, "RTX 3060 Ti, 8192, 4500")):
                name, total, available = inv._gpu()
        self.assertEqual(name, "RTX 3060 Ti")
        self.assertEqual(total, 8192 * 1024 * 1024)
        self.assertEqual(available, 4500 * 1024 * 1024)

    def test_unavailable_gpu_does_not_claim_8gb(self):
        with patch.object(inv.shutil, "which", return_value=None):
            self.assertEqual(inv._gpu(), ("", None, None))

    def test_ollama_api_only_uses_fixed_loopback(self):
        urls = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, length): return b'{"models":[]}'
        class Opener:
            def open(self, request, timeout):
                urls.append((request.full_url, timeout))
                return Response()
        with patch.object(inv.request, "build_opener", return_value=Opener()) as builder:
            result = inv._api_json("/api/tags")
        self.assertEqual(result, {"models": []})
        self.assertEqual(urls, [("http://127.0.0.1:11434/api/tags", 1.5)])
        self.assertTrue(any(isinstance(handler, inv._NoRedirect)
                            for handler in builder.call_args.args))

    def test_oversized_api_reply_is_not_loaded_or_trusted(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, length):
                self.requested = length
                return b"x" * length
        response = Response()
        class Opener:
            def open(self, *args, **kwargs): return response
        with patch.object(inv.request, "build_opener", return_value=Opener()):
            with self.assertRaises(ValueError):
                inv._api_json("/api/version")
        self.assertEqual(response.requested, 1024 * 1024 + 1)

    def test_scan_reuses_existing_folder_without_installing(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "New Folder" / "Atlas"
            with patch.object(inv, "_program", return_value=inv.Component("NOT_FOUND")), \
                 patch.object(inv, "_ollama", return_value=(inv.Component("NOT_FOUND"), ())), \
                 patch.object(inv, "_gpu", return_value=("", None, None)), \
                 patch.object(inv, "_ram_total", return_value=32 * 1024**3):
                snapshot = inv.scan(target)
            self.assertFalse(target.exists())
            self.assertGreater(snapshot.installation_disk_free_bytes, 0)
            self.assertEqual(snapshot.ollama.status, "NOT_FOUND")
            self.assertEqual(snapshot.models, ())
            self.assertEqual(snapshot.ram_total_bytes, 32 * 1024**3)

    def test_relative_target_rejected(self):
        with self.assertRaises(ValueError):
            inv.scan(Path("relative/target"))


if __name__ == "__main__":
    unittest.main()

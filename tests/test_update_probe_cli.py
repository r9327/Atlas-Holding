import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

MANIFEST = {
    "schema": "atlas-update-manifest/v1", "release": "v2.0.0", "channel": "test",
    "artifacts": [
        {"path": "app/Atlas.zip", "sha256": "d" * 64, "size": 100, "patches": []}
    ],
}


class Fixture(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        Fixture.hits += 1
        if self.headers.get("If-None-Match") == '"v2"':
            self.send_response(304)
            self.end_headers()
            return
        body = json.dumps(MANIFEST).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", '"v2"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ProbeCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/manifest.json"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)

    def setUp(self):
        Fixture.hits = 0
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "cache.json"

    def call(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "holding.update_probe_cli",
             "--manifest-url", self.url, "--cache-file", str(self.cache),
             "--allow-test-loopback", *extra],
            capture_output=True, text=True, check=False,
        )

    def test_works_across_process_restarts_and_skips_second_request(self):
        first = self.call()
        self.assertEqual(first.returncode, 0, first.stderr)
        result = json.loads(first.stdout)
        self.assertEqual(result["release"], "v2.0.0")
        self.assertEqual(result["status"], "UPDATED")
        self.assertTrue(result["revalidated"])
        self.assertFalse(result["installation_performed"])
        again = self.call()
        self.assertEqual(again.returncode, 0, again.stderr)
        result = json.loads(again.stdout)
        self.assertEqual(result["status"], "CACHE_NOT_REVALIDATED")
        self.assertFalse(result["revalidated"])
        self.assertEqual(result["network_bytes"], 0)
        self.assertEqual(Fixture.hits, 1)

    def test_explicit_zero_ttl_forces_etag_check(self):
        self.call()
        again = self.call("--max-cache-age", "0")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["status"], "UNCHANGED_VALIDATED")
        self.assertEqual(Fixture.hits, 2)

    def test_production_refuses_plain_http_without_test_optin(self):
        p = subprocess.run(
            [sys.executable, "-m", "holding.update_probe_cli",
             "--manifest-url", self.url, "--cache-file", str(self.cache)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stderr)["status"], "UNAVAILABLE")
        self.assertEqual(Fixture.hits, 0)

    def test_invalid_ttl_rejected_before_network(self):
        result = self.call("--max-cache-age", "4000")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(Fixture.hits, 0)


if __name__ == "__main__":
    unittest.main()

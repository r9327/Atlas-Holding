import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from holding.update_manifest_client import ManifestFetchError
from holding.update_probe import probe_release

MANIFEST = {
    "schema": "atlas-update-manifest/v1", "release": "v1.2.3",
    "channel": "stable",
    "artifacts": [{"path": "app/Atlas.zip", "sha256": "a" * 64, "size": 200, "patches": []}],
}


class Handler(BaseHTTPRequestHandler):
    calls = []
    bad_manifest = False

    def do_GET(self):
        etag = self.headers.get("If-None-Match")
        type(self).calls.append(etag)
        if etag == '"abc123"':
            self.send_response(304)
            self.send_header("ETag", '"abc123"')
            self.end_headers()
            return
        contents = {**MANIFEST, "schema": "invalid"} if self.bad_manifest else MANIFEST
        data = json.dumps(contents).encode()
        self.send_response(200)
        self.send_header("ETag", '"abc123"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/manifest.json"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        Handler.calls.clear()
        Handler.bad_manifest = False
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "store" / "check.json"

    def probe(self, **kwargs):
        opts = {"cache_file": self.cache, "now_epoch": 1000,
                "allow_test_loopback": True}
        opts.update(kwargs)
        return probe_release(self.url, **opts)

    def test_first_probe_downloads_valid_metadata_only(self):
        result = self.probe()
        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(result.release, "v1.2.3")
        self.assertEqual(result.channel, "stable")
        self.assertEqual(result.etag, '"abc123"')
        self.assertGreater(result.network_bytes, 0)
        self.assertEqual(Handler.calls, [None])
        self.assertTrue(self.cache.exists())

    def test_repeated_startup_skips_network_with_honest_cache_label(self):
        self.probe()
        result = self.probe(now_epoch=1010)
        self.assertEqual(result.status, "CACHE_NOT_REVALIDATED")
        self.assertIsNone(result.manifest)
        self.assertEqual(result.network_bytes, 0)
        self.assertEqual(Handler.calls, [None])

    def test_expired_probe_checks_etag_304_without_payload(self):
        self.probe()
        result = self.probe(now_epoch=1040)
        self.assertEqual(result.status, "UNCHANGED_VALIDATED")
        self.assertEqual(result.release, "v1.2.3")
        self.assertEqual(result.network_bytes, 0)
        self.assertEqual(Handler.calls, [None, '"abc123"'])
        again = self.probe(now_epoch=1041)
        self.assertEqual(again.status, "CACHE_NOT_REVALIDATED")
        self.assertEqual(len(Handler.calls), 2)

    def test_invalid_new_manifest_does_not_create_a_cache(self):
        Handler.bad_manifest = True
        with self.assertRaises(ManifestFetchError):
            self.probe()
        self.assertFalse(self.cache.exists())

    def test_reopen_from_disk_in_a_second_probe(self):
        self.probe()
        self.assertEqual(self.probe(now_epoch=1005).status, "CACHE_NOT_REVALIDATED")

    def test_other_endpoint_does_not_reuse_old_etag(self):
        self.probe()
        alt = self.url + "?channel=test"
        result = probe_release(
            alt, cache_file=self.cache, now_epoch=1001,
            allow_test_loopback=True,
        )
        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(Handler.calls, [None, None])

    def test_invalid_max_cache_ttl_refused_before_network(self):
        with self.assertRaises(ManifestFetchError):
            self.probe(max_cache_age_seconds=4000)
        self.assertEqual(Handler.calls, [])

    def test_unchanged_result_is_not_a_certification_or_install(self):
        self.probe()
        result = self.probe(now_epoch=1050)
        self.assertEqual(result.status, "UNCHANGED_VALIDATED")
        self.assertIsNone(result.manifest)
        self.assertFalse((self.cache.parent / "versions").exists())


if __name__ == "__main__":
    unittest.main()

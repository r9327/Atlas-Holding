import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from holding.update_manifest_client import ManifestFetchError, check_manifest

MANIFEST = {
    "schema": "atlas-update-manifest/v1", "release": "v1.0.1", "channel": "stable",
    "artifacts": [{"path": "app/app.zip", "sha256": "a" * 64, "size": 100, "patches": []}],
}


class ManifestHandler(BaseHTTPRequestHandler):
    requests = []
    payload = MANIFEST
    redirect_to = None

    def do_GET(self):
        if self.redirect_to is not None:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
        etag = self.headers.get("If-None-Match")
        type(self).requests.append(etag)
        if etag == '"v1"':
            self.send_response(304)
            self.send_header("ETag", '"v1"')
            self.end_headers()
            return
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("ETag", '"v1"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ManifestClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ManifestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/releases/manifest.json"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        ManifestHandler.requests.clear()
        ManifestHandler.payload = MANIFEST
        ManifestHandler.redirect_to = None

    def check(self, **overrides):
        return check_manifest(self.url, allow_test_loopback=True, **overrides)

    def test_first_fetch_returns_validated_manifest(self):
        found = self.check()
        self.assertEqual(found.status, "UPDATED")
        self.assertEqual(found.manifest.release, "v1.0.1")
        self.assertEqual(found.etag, '"v1"')
        self.assertGreater(found.bytes_received, 0)

    def test_subsequent_304_downloads_zero_bytes(self):
        first = self.check()
        second = self.check(previous_etag=first.etag)
        self.assertEqual(second.status, "UNCHANGED")
        self.assertIsNone(second.manifest)
        self.assertEqual(second.bytes_received, 0)
        self.assertEqual(ManifestHandler.requests, [None, '"v1"'])

    def test_refuses_invalid_manifest_without_cache_promotion(self):
        ManifestHandler.payload = {**MANIFEST, "schema": "unknown/v0"}
        with self.assertRaises(ManifestFetchError):
            self.check()

    def test_http_public_url_never_contacted(self):
        with self.assertRaises(ManifestFetchError):
            check_manifest("http://example.com/releases.json")
        self.assertEqual(ManifestHandler.requests, [])

    def test_redirect_to_insecure_origin_fails_before_following(self):
        ManifestHandler.redirect_to = "http://example.invalid/unsafe"
        with self.assertRaisesRegex(ManifestFetchError, "network error"):
            self.check(previous_etag='"v1"')
        self.assertEqual(ManifestHandler.requests, [])

    def test_refuses_untrusted_cache_etag(self):
        with self.assertRaises(ManifestFetchError):
            self.check(previous_etag="evil\\r\\nInjected: yes")
        self.assertEqual(ManifestHandler.requests, [])

    def test_refuses_oversized_manifest(self):
        ManifestHandler.payload = {**MANIFEST, "padding": "x" * (256 * 1024)}
        with self.assertRaises(ManifestFetchError):
            self.check()


if __name__ == "__main__":
    unittest.main()

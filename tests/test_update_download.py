import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from holding.update_download import ArtifactDownloadError, download_staged_artifact

PAYLOAD = bytes(range(256)) * 100
HASH = hashlib.sha256(PAYLOAD).hexdigest()


class ArtifactHandler(BaseHTTPRequestHandler):
    requests = []
    ignore_range = False
    bad_etag = False

    def do_GET(self):
        rng = self.headers.get("Range")
        type(self).requests.append((rng, self.headers.get("If-Range")))
        start = 0
        if rng and not self.ignore_range:
            try:
                start = int(rng.removeprefix("bytes=").removesuffix("-"))
            except ValueError:
                self.send_error(400)
                return
        status = 206 if start else 200
        body = PAYLOAD[start:]
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("ETag", '"changed"' if self.bad_etag else '"v1"')
        self.send_header("Content-Length", str(len(body)))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class UpdateDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ArtifactHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/blob"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        ArtifactHandler.requests.clear()
        ArtifactHandler.ignore_range = False
        ArtifactHandler.bad_etag = False
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "staging" / "artifact.blob"

    def download(self, **overrides):
        args = {
            "expected_sha256": HASH,
            "expected_size": len(PAYLOAD),
            "allow_test_loopback": True,
            "chunk_bytes": 4096,
        }
        args.update(overrides)
        return download_staged_artifact(self.url, self.path, **args)

    def test_full_download_is_verified_before_publishing(self):
        self.assertEqual(self.download().read_bytes(), PAYLOAD)
        self.assertFalse(self.path.with_name(self.path.name + ".part").exists())
        self.assertEqual(ArtifactHandler.requests, [(None, None)])

    def test_progress_callback_reports_received_bytes(self):
        points = []
        self.download(on_progress=lambda got, total: points.append((got, total)))
        self.assertTrue(points)
        self.assertEqual(points[0], (0, len(PAYLOAD)))
        self.assertEqual(points[-1], (len(PAYLOAD), len(PAYLOAD)))
        self.assertEqual([x[0] for x in points], sorted(x[0] for x in points))
        self.assertTrue(all(total == len(PAYLOAD) for _, total in points))

    def test_resume_requires_etag_and_validates_content_range(self):
        self.path.parent.mkdir()
        self.path.with_name(self.path.name + ".part").write_bytes(PAYLOAD[:4096])
        self.path.with_name(self.path.name + ".part.etag").write_text('"v1"')
        self.assertEqual(self.download().read_bytes(), PAYLOAD)
        self.assertEqual(ArtifactHandler.requests, [("bytes=4096-", '"v1"')])

    def test_server_ignores_range_restarts_safely(self):
        ArtifactHandler.ignore_range = True
        self.path.parent.mkdir()
        self.path.with_name(self.path.name + ".part").write_bytes(PAYLOAD[:4096])
        self.path.with_name(self.path.name + ".part.etag").write_text('"v1"')
        self.assertEqual(self.download().read_bytes(), PAYLOAD)
        self.assertEqual(ArtifactHandler.requests, [("bytes=4096-", '"v1"')])

    def test_partial_without_etag_restarts_safely(self):
        self.path.parent.mkdir()
        self.path.with_name(self.path.name + ".part").write_bytes(PAYLOAD[:4096])
        self.download()
        self.assertEqual(ArtifactHandler.requests, [(None, None)])
        self.assertEqual(self.path.read_bytes(), PAYLOAD)

    def test_bad_checksum_does_not_publish_artifact(self):
        with self.assertRaisesRegex(ArtifactDownloadError, "SHA-256 mismatch"):
            self.download(expected_sha256="0" * 64)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_name(self.path.name + ".part").exists())

    def test_bad_etag_does_not_publish_artifact(self):
        self.path.parent.mkdir()
        self.path.with_name(self.path.name + ".part").write_bytes(PAYLOAD[:4096])
        self.path.with_name(self.path.name + ".part.etag").write_text('"v1"')
        ArtifactHandler.bad_etag = True
        with self.assertRaisesRegex(ArtifactDownloadError, "ETag"):
            self.download()
        self.assertFalse(self.path.exists())

    def test_reject_insecure_public_url_and_existing_output(self):
        with self.assertRaisesRegex(ArtifactDownloadError, "HTTPS"):
            download_staged_artifact("http://example.com", self.path,
                                     expected_sha256=HASH, expected_size=len(PAYLOAD))
        self.path.parent.mkdir()
        self.path.write_bytes(b"do not overwrite")
        with self.assertRaisesRegex(ArtifactDownloadError, "already exists"):
            self.download()
        self.assertEqual(self.path.read_bytes(), b"do not overwrite")

    def test_reject_wrong_declared_size_without_publishing(self):
        with self.assertRaisesRegex(ArtifactDownloadError, "download size"):
            self.download(expected_size=len(PAYLOAD) - 1)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()

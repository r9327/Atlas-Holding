"""Local network test server. No Internet or third-party packages needed."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from holding.downloads import download_pinned, DownloadError, IntegrityError

DATA = (b"Holding reliable bootstrap\n" * 65536) + b"END"
HASH = hashlib.sha256(DATA).hexdigest()


class Server(BaseHTTPRequestHandler):
    behavior = "range"
    drops = 0

    def log_message(self, *_):
        return

    def do_GET(self):
        data = DATA
        start = 0
        r = self.headers.get("Range")
        if r and self.behavior != "ignore_range":
            start = int(r.split("=")[1].split("-")[0])
            self.send_response(206)
            declared_start = 0 if self.behavior == "bad_range" else start
            self.send_header("Content-Range", f"bytes {declared_start}-{len(data)-1}/{len(data)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        if self.behavior == "drop_once" and self.__class__.drops == 0:
            self.__class__.drops += 1
            self.wfile.write(data[start : start + 5000])
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
        else:
            self.wfile.write(data[start:])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        Server.behavior = "range"
        Server.drops = 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/asset"
        self.target = Path(self.temp.name) / "setup.bin"

    def call(self, **kwargs):
        return download_pinned(self.url, self.target, sha256=HASH, size=len(DATA), retry_delay=0, **kwargs)

    def test_normal_and_existing(self):
        self.assertEqual(self.call().read_bytes(), DATA)
        self.assertEqual(self.call().read_bytes(), DATA)

    def test_resume_after_drop_and_process_restart(self):
        Server.behavior = "drop_once"
        with self.assertRaises(DownloadError):
            self.call(max_attempts=1)
        partial = self.target.with_name(self.target.name + ".part")
        self.assertGreater(partial.stat().st_size, 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.call().read_bytes(), DATA)
        self.assertFalse(partial.exists())

    def test_range_ignored_restarts_instead_of_appending(self):
        partial = self.target.with_name(self.target.name + ".part")
        partial.write_bytes(DATA[:70000])
        Server.behavior = "ignore_range"
        self.assertEqual(self.call().read_bytes(), DATA)

    def test_full_partial_reusable_without_network(self):
        partial = self.target.with_name(self.target.name + ".part")
        partial.write_bytes(DATA)
        self.httpd.shutdown()
        self.assertEqual(self.call().read_bytes(), DATA)

    def test_corrupt_not_published(self):
        partial = self.target.with_name(self.target.name + ".part")
        partial.write_bytes(b"x" * len(DATA))
        with self.assertRaises(IntegrityError):
            self.call()
        self.assertFalse(self.target.exists())
        self.assertTrue(partial.exists())

    def test_invalid_range_header_preserves_partial(self):
        partial = self.target.with_name(self.target.name + ".part")
        partial.write_bytes(DATA[:70000])
        Server.behavior = "bad_range"
        with self.assertRaises(DownloadError):
            self.call()
        self.assertEqual(partial.read_bytes(), DATA[:70000])
        self.assertFalse(self.target.exists())

    def test_symlink_is_refused(self):
        other = Path(self.temp.name) / "other"
        other.write_bytes(DATA)
        self.target.symlink_to(other)
        with self.assertRaises(DownloadError):
            self.call()

    def test_invalid_url_and_digest(self):
        with self.assertRaises(ValueError):
            download_pinned("http://example.org/a", self.target, sha256=HASH, size=len(DATA))
        with self.assertRaises(ValueError):
            download_pinned(self.url, self.target, sha256="bad", size=len(DATA))


if __name__ == "__main__":
    unittest.main()

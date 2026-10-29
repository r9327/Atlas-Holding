"""Local network test server. No Internet or third-party packages needed."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from holding import downloads
from holding.downloads import (
    download_pinned,
    DownloadBusyError,
    DownloadCancelled,
    DownloadError,
    InsufficientSpaceError,
    IntegrityError,
    TransferJournalError,
)

DATA = (b"Holding reliable bootstrap\n" * 65536) + b"END"
HASH = hashlib.sha256(DATA).hexdigest()


class Server(BaseHTTPRequestHandler):
    behavior = "range"
    drops = 0

    def log_message(self, *_):
        return

    def do_GET(self):
        if self.behavior == "not_found":
            self.send_error(404)
            return
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

    def test_download_ignores_unusable_ambient_proxy_configuration(self):
        with patch.object(downloads, "build_opener",
                          wraps=downloads.build_opener) as opener:
            self.assertEqual(self.call().read_bytes(), DATA)
        handlers = opener.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], downloads._SafeRedirect)

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

    def test_concurrent_destination_is_never_overwritten(self):
        intruder = b"file-created-by-another-process"

        def create_destination(_count, _size):
            if not self.target.exists():
                self.target.write_bytes(intruder)

        with self.assertRaisesRegex(IntegrityError, "appeared during download"):
            self.call(progress=create_destination)
        self.assertEqual(self.target.read_bytes(), intruder)
        partial = self.target.with_name(self.target.name + ".part")
        self.assertEqual(partial.read_bytes(), DATA)

    def test_invalid_range_header_preserves_partial(self):
        partial = self.target.with_name(self.target.name + ".part")
        partial.write_bytes(DATA[:70000])
        Server.behavior = "bad_range"
        with self.assertRaises(DownloadError):
            self.call()
        self.assertEqual(partial.read_bytes(), DATA[:70000])
        self.assertFalse(self.target.exists())
        journal = json.loads(
            self.target.with_name(self.target.name + ".transfer.json").read_text()
        )
        self.assertEqual(journal["state"], "FAILED")
        self.assertEqual(journal["partial_bytes"], 70000)

    def test_symlink_is_refused(self):
        other = Path(self.temp.name) / "other"
        other.write_bytes(DATA)
        self.target.symlink_to(other)
        with self.assertRaises(DownloadError):
            self.call()

    def test_linked_parent_is_refused_before_download(self):
        with patch.object(downloads, "_is_link_like",
                          side_effect=lambda path: path == self.target.parent):
            with self.assertRaisesRegex(DownloadError, "symlink or junction"):
                self.call()

    def test_hardlinked_partial_is_refused(self):
        original = Path(self.temp.name) / "shared-partial"
        original.write_bytes(DATA[:100])
        partial = self.target.with_name(self.target.name + ".part")
        os.link(original, partial)
        with self.assertRaisesRegex(DownloadError, "hard links"):
            self.call()
        self.assertEqual(original.read_bytes(), DATA[:100])

    def test_second_writer_is_rejected_without_network_or_mutation(self):
        with downloads._target_lock(self.target):
            with self.assertRaises(DownloadBusyError):
                self.call()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_name(self.target.name + ".part").exists())

    def test_insufficient_space_is_reported_before_network(self):
        usage = shutil._ntuple_diskusage(total=1000, used=900, free=100)
        with patch.object(downloads.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(InsufficientSpaceError, "100 free bytes"):
                self.call(free_space_reserve=0)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_name(self.target.name + ".part").exists())

    def test_space_check_accounts_for_resumable_bytes(self):
        partial = self.target.with_name(self.target.name + ".part")
        partial.write_bytes(DATA[:-64])
        usage = shutil._ntuple_diskusage(total=1024, used=960, free=64)
        with patch.object(downloads.shutil, "disk_usage", return_value=usage):
            self.assertEqual(self.call(free_space_reserve=0).read_bytes(), DATA)

    def test_server_ignoring_range_rechecks_space_for_full_restart(self):
        partial = self.target.with_name(self.target.name + ".part")
        partial.write_bytes(DATA[:-64])
        Server.behavior = "ignore_range"
        usage = shutil._ntuple_diskusage(total=len(DATA), used=64, free=len(DATA) - 64)
        with patch.object(downloads.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(InsufficientSpaceError):
                self.call(free_space_reserve=0)
        self.assertEqual(partial.read_bytes(), DATA[:-64])
        journal = json.loads(
            self.target.with_name(self.target.name + ".transfer.json").read_text()
        )
        self.assertEqual(journal["state"], "BLOCKED")

    def test_cancellation_preserves_partial_for_later_resume(self):
        observed = 0

        def progress(count, _size):
            nonlocal observed
            observed = count

        with self.assertRaises(DownloadCancelled):
            self.call(progress=progress, cancelled=lambda: observed > 0)
        partial = self.target.with_name(self.target.name + ".part")
        self.assertEqual(partial.stat().st_size, observed)
        self.assertGreater(observed, 0)
        self.assertFalse(self.target.exists())
        journal = json.loads(
            self.target.with_name(self.target.name + ".transfer.json").read_text()
        )
        self.assertEqual(journal["state"], "CANCELLED")
        self.assertEqual(journal["partial_bytes"], observed)
        self.assertEqual(journal["attempts"], 1)
        self.assertEqual(self.call().read_bytes(), DATA)
        complete = json.loads(
            self.target.with_name(self.target.name + ".transfer.json").read_text()
        )
        self.assertEqual(complete["state"], "COMPLETE")
        self.assertEqual(complete["attempts"], 2)

    def test_cancellation_before_request_creates_no_partial(self):
        with self.assertRaisesRegex(DownloadCancelled, "before the next request"):
            self.call(cancelled=lambda: True)
        self.assertFalse(self.target.with_name(self.target.name + ".part").exists())

    def test_journal_omits_url_query_secrets_and_rejects_new_pin(self):
        secret_url = self.url + "?token=must-not-persist"
        download_pinned(
            secret_url, self.target, sha256=HASH, size=len(DATA),
            retry_delay=0, manifest_id="bootstrap-v1",
        )
        journal_path = self.target.with_name(self.target.name + ".transfer.json")
        raw = journal_path.read_text(encoding="utf-8")
        self.assertNotIn("must-not-persist", raw)
        self.assertEqual(json.loads(raw)["url"], self.url)
        with self.assertRaisesRegex(TransferJournalError, "pinned manifest"):
            download_pinned(
                secret_url, self.target, sha256=HASH, size=len(DATA),
                retry_delay=0, manifest_id="bootstrap-v2",
            )

    def test_interrupted_attempt_is_persisted_for_restart(self):
        Server.behavior = "drop_once"
        with self.assertRaises(DownloadError):
            self.call(max_attempts=1, manifest_id="drop-test")
        journal = json.loads(
            self.target.with_name(self.target.name + ".transfer.json").read_text()
        )
        partial = self.target.with_name(self.target.name + ".part")
        self.assertEqual(journal["state"], "INTERRUPTED")
        self.assertEqual(journal["partial_bytes"], partial.stat().st_size)
        self.assertEqual(journal["attempts"], 1)
        self.assertTrue(journal["error"])

    def test_permanent_http_failure_is_not_retried_and_is_journaled(self):
        Server.behavior = "not_found"
        with self.assertRaisesRegex(DownloadError, "Permanent HTTP 404"):
            self.call(max_attempts=3)
        journal = json.loads(
            self.target.with_name(self.target.name + ".transfer.json").read_text()
        )
        self.assertEqual(journal["state"], "FAILED")
        self.assertEqual(journal["attempts"], 1)
        self.assertEqual(journal["error"], "HTTP_404")

    def test_invalid_url_and_digest(self):
        with self.assertRaises(ValueError):
            download_pinned("http://example.org/a", self.target, sha256=HASH, size=len(DATA))
        with self.assertRaises(ValueError):
            download_pinned(self.url, self.target, sha256="bad", size=len(DATA))
        with self.assertRaises(ValueError):
            self.call(free_space_reserve=-1)


if __name__ == "__main__":
    unittest.main()

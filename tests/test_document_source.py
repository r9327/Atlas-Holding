import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from holding.document_source import DocumentSourceError, fetch_document

JSON_BODY = b'[{"id":42,"kind":"quest","label":"Source Web"}]'
CSV_BODY = b'id,kind,label\n001,quest,Premiere\n002,success,Seconde\n'


class DocumentHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_GET(self):
        type(self).calls += 1
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.org/internal")
            self.end_headers()
            return
        if self.path == "/local-redirect":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.end_headers()
            return
        if self.path == "/wrong":
            content_type = "text/html"
            payload = JSON_BODY
        elif self.path == "/too-large":
            content_type = "application/json"
            payload = JSON_BODY * 100
        elif self.path == "/bad-json":
            content_type = "application/json"
            payload = b'[{"id":1},{"id":1}]'
        elif self.path == "/csv":
            content_type = "text/csv"
            payload = CSV_BODY
        elif self.path == "/markdown":
            content_type = "text/markdown"
            payload = b"| id | kind |\n| --- | --- |\n| 009 | quest |\n"
        elif self.path == "/html":
            content_type = "text/html"
            payload = b"<table><tr><th>id</th><th>kind</th></tr><tr><td>009</td><td>quest</td></tr></table>"
        else:
            content_type = "application/json"
            payload = JSON_BODY
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class DocumentSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DocumentHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        DocumentHandler.calls = 0

    def collect(self, path, *, format="json", **overrides):
        params = {
            "allowed_hosts": ("127.0.0.1",),
            "format": format,
            "allow_test_loopback": True,
        }
        params.update(overrides)
        return fetch_document(self.origin + path, **params)

    def test_json_over_real_http(self):
        result = self.collect("/json")
        self.assertEqual(result.bytes_received, len(JSON_BODY))
        self.assertEqual(result.batch.records[0].record_id, "42")
        self.assertEqual(json.loads(result.batch.records[0].canonical_json)["kind"], "quest")
        self.assertEqual(result.batch.source, "local:" + self.origin + "/json")
        self.assertEqual(DocumentHandler.calls, 1)

    def test_csv_over_real_http_preserves_leading_zeroes(self):
        result = self.collect("/csv", format="csv")
        self.assertEqual([r.record_id for r in result.batch.records], ["001", "002"])
        self.assertEqual(result.content_type, "text/csv")

    def test_html_table_over_real_http(self):
        result = self.collect("/html", format="html")
        self.assertEqual(result.batch.records[0].record_id, "009")
        self.assertEqual(result.content_type, "text/html")

    def test_markdown_table_over_real_http(self):
        result = self.collect("/markdown", format="markdown")
        self.assertEqual(result.batch.records[0].record_id, "009")
        self.assertEqual(result.content_type, "text/markdown")

    def test_same_host_redirect_is_allowed(self):
        data = self.collect("/local-redirect")
        self.assertEqual(data.batch.records[0].record_id, "42")
        self.assertEqual(DocumentHandler.calls, 2)

    def test_cross_host_redirect_refused_before_following(self):
        with self.assertRaisesRegex(DocumentSourceError, "approved"):
            self.collect("/redirect")
        self.assertEqual(DocumentHandler.calls, 1)

    def test_invalid_content_type_or_duplicate_id(self):
        for path in ("/wrong", "/bad-json"):
            with self.subTest(path=path), self.assertRaises(DocumentSourceError):
                self.collect(path)

    def test_transfer_size_budget_is_enforced(self):
        with self.assertRaisesRegex(DocumentSourceError, "size budget"):
            self.collect("/too-large", max_bytes=100)

    def test_remote_http_is_never_contacted(self):
        with self.assertRaisesRegex(DocumentSourceError, "HTTPS"):
            fetch_document("http://example.org/quest", allowed_hosts=("example.org",), format="json")
        self.assertEqual(DocumentHandler.calls, 0)

    def test_unapproved_local_http_is_never_contacted(self):
        with self.assertRaises(DocumentSourceError):
            fetch_document(self.origin + "/json", allowed_hosts=("127.0.0.1",), format="json")
        self.assertEqual(DocumentHandler.calls, 0)

    def test_invalid_configuration_is_refused_before_http(self):
        for kwargs in ({"allowed_hosts": ()}, {"allowed_hosts": ("invalid/path",)},
                       {"format": "xml"}, {"max_bytes": 0}):
            args = {"allowed_hosts": ("127.0.0.1",), "format": "json",
                    "allow_test_loopback": True}
            args.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(DocumentSourceError):
                fetch_document(self.origin + "/json", **args)
        self.assertEqual(DocumentHandler.calls, 0)


if __name__ == "__main__":
    unittest.main()

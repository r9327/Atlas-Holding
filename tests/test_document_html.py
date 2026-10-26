import json
import unittest

from holding.document_html import ingest_html_table
from holding.document_intake import DocumentIntakeError, diff_records

STAMP = "2026-10-22T18:30:00Z"


def parse(html: str, **kwargs):
    return ingest_html_table(html.encode("utf-8"), source="local:page.html", collected_at=STAMP, **kwargs)


class HtmlTableTests(unittest.TestCase):
    def test_static_html_table_extracts_normalized_rows(self):
        batch = parse('''<html><table><thead><tr><th>id</th><th>kind</th>
        <th>label</th></tr></thead><tbody>
        <tr><td>002</td><td>quest</td><td>Début <strong>d'une quête</strong></td></tr>
        <tr><td>001</td><td>success</td><td>&amp; autres</td></tr>
        </tbody></table></html>''')
        self.assertEqual([r.record_id for r in batch.records], ["001", "002"])
        self.assertEqual(json.loads(batch.records[0].canonical_json)["label"], "& autres")
        self.assertEqual(json.loads(batch.records[1].canonical_json)["label"], "Début d'une quête")

    def test_select_second_table_by_index(self):
        html = '''<table><tr><th>id</th></tr><tr><td>9</td></tr></table>
        <table><tr><th>id</th><th>name</th></tr><tr><td>42</td><td>Guide</td></tr></table>'''
        self.assertEqual(parse(html, table_index=1).records[0].record_id, "42")

    def test_ignore_script_content_and_never_execute(self):
        html = '''<table><tr><th>id</th><th>label</th></tr>
        <tr><td>7</td><td>hello<script>window.alert("bad")</script> world</td></tr></table>'''
        batch = parse(html)
        self.assertEqual(json.loads(batch.records[0].canonical_json)["label"], "hello world")

    def test_duplicate_id_and_bad_header_rejected(self):
        for html in (
            "<table><tr><th>id</th></tr><tr><td>1</td></tr><tr><td>1</td></tr></table>",
            "<table><tr><th>name</th></tr><tr><td>x</td></tr></table>",
            "<table><tr><td>id</td></tr><tr><td>1</td></tr></table>",
            "<table><tr><th>id</th><th>id</th></tr><tr><td>1</td><td>1</td></tr></table>",
        ):
            with self.subTest(html=html), self.assertRaises(DocumentIntakeError):
                parse(html)

    def test_refuse_merged_cells_and_missing_columns(self):
        for html in (
            "<table><tr><th>id</th></tr><tr><td colspan='2'>1</td></tr></table>",
            "<table><tr><th>id</th><th>label</th></tr><tr><td>1</td></tr></table>",
        ):
            with self.subTest(html=html), self.assertRaises(DocumentIntakeError):
                parse(html)

    def test_absent_rows_are_not_deleted(self):
        before = parse("<table><tr><th>id</th></tr><tr><td>1</td></tr></table>")
        after = parse("<table><tr><th>id</th></tr><tr><td>2</td></tr></table>")
        self.assertEqual([(x.record_id, x.status) for x in diff_records(before, after)],
                         [("1", "missing"), ("2", "added")])

    def test_missing_or_unclosed_table_rejected(self):
        for html in ("<p>Nothing here</p>", "<table><tr><th>id</th></tr>"):
            with self.subTest(html=html), self.assertRaises(DocumentIntakeError):
                parse(html)


if __name__ == "__main__":
    unittest.main()

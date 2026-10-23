import json
import unittest

from holding.document_markdown import ingest_markdown_table
from holding.document_intake import DocumentIntakeError, diff_records

STAMP = "2026-10-23T18:00:00Z"


def parse(text, **kwargs):
    return ingest_markdown_table(
        text.encode("utf-8"), source="local:guides.md",
        collected_at=STAMP, **kwargs,
    )


class MarkdownTests(unittest.TestCase):
    def test_normalized_data_and_escaped_pipes(self):
        batch = parse(r"""# Guide
| id | category | title |
| --- | :--- | ---: |
| 002 | quest | La \| fuite |
| 001 | success | **Début** |
""")
        self.assertEqual([r.record_id for r in batch.records], ["001", "002"])
        self.assertEqual(json.loads(batch.records[1].canonical_json)["title"], "La | fuite")
        self.assertEqual(json.loads(batch.records[0].canonical_json)["title"], "**Début**")

    def test_code_fences_are_ignored(self):
        fence = chr(96) * 3
        batch = parse(
            fence + "\n| id | label |\n| --- | --- |\n| 999 | never |\n"
            + fence + "\n| id | label |\n| --- | --- |\n| 2 | yes |\n"
        )
        self.assertEqual([r.record_id for r in batch.records], ["2"])

    def test_multiple_tables_select_explicitly(self):
        text = """| id | label |
| --- | --- |
| 1 | first |

| id | label |
| --- | --- |
| 2 | second |
"""
        self.assertEqual(parse(text, table_index=1).records[0].record_id, "2")

    def test_duplicate_and_bad_headers_fail_closed(self):
        for value in (
            "| id | label |\n| --- | --- |\n| 1 | A |\n| 1 | B |",
            "| id | id |\n| --- | --- |\n| 1 | 1 |",
            "| name | title |\n| --- | --- |\n| A | B |",
            "| id | title |\n| --- | --- |\n| 1 | A |\n| 2 | B | C |",
            "No tabular data at all",
        ):
            with self.subTest(value=value), self.assertRaises(DocumentIntakeError):
                parse(value)

    def test_missing_rows_are_not_deleted(self):
        before = parse("| id | name |\n| --- | --- |\n| 01 | Old |")
        after = parse("| id | name |\n| --- | --- |\n| 02 | New |")
        self.assertEqual([(r.record_id, r.status) for r in diff_records(before, after)],
                         [("01", "missing"), ("02", "added")])
        self.assertEqual(len(before.source_sha256), 64)


if __name__ == "__main__":
    unittest.main()

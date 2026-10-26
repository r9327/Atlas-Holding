import json
import unittest

from holding.document_csv import ingest_csv_records
from holding.document_intake import DocumentIntakeError, diff_records

STAMP = "2026-10-22T17:30:00+02:00"


def parse(s, source="local:csv"):
    return ingest_csv_records(s.encode("utf-8"), source=source, collected_at=STAMP)


class DocumentCsvTests(unittest.TestCase):
    def test_preserves_ids_and_quoted_fields(self):
        result = parse('id,label,notes\n002,"Quête, suite","a ""quoted"" note"\n001,Succès,Ok\n')
        self.assertEqual([r.record_id for r in result.records], ["001", "002"])
        self.assertEqual(json.loads(result.records[1].canonical_json)["label"], "Quête, suite")
        self.assertEqual(result.collected_at, "2026-10-22T15:30:00Z")

    def test_csv_header_bom(self):
        result = ingest_csv_records(b"\xef\xbb\xbfid,name\n7,hello\n",
                                    source="local:fixture", collected_at=STAMP)
        self.assertEqual(result.records[0].record_id, "7")

    def test_changes_detected_without_deletions(self):
        old = parse("id,label\n1,old\n2,keep\n")
        new = parse("id,label\n1,new\n3,added\n")
        self.assertEqual([(r.record_id, r.status) for r in diff_records(old, new)],
                         [("1", "changed"), ("2", "missing"), ("3", "added")])

    def test_invalid_columns_rejected(self):
        for s in ("id,id\n1,2\n", "name,title\nX,Y\n",
                  "id,name\n1,A,extra\n", "id,name\n1\n", "\n"):
            with self.subTest(csv=s), self.assertRaises(DocumentIntakeError):
                parse(s)

    def test_duplicate_ids_and_bad_utf8(self):
        with self.assertRaises(DocumentIntakeError):
            parse("id,name\n2,a\n2,b\n")
        with self.assertRaises(DocumentIntakeError):
            ingest_csv_records(b"\xff", source="local:fixture", collected_at=STAMP)

    def test_malformed_quote_refused(self):
        with self.assertRaises(DocumentIntakeError):
            parse('id,label\n1,"not closed\n')


if __name__ == "__main__":
    unittest.main()

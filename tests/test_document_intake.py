import json
import unittest

from holding.document_intake import (
    DocumentIntakeError, diff_records, find_cross_source_conflicts,
    ingest_json_records,
)

STAMP = "2026-10-22T17:30:00+02:00"


def batch(items, *, source="local:fixture"):
    return ingest_json_records(json.dumps(items).encode(), source=source, collected_at=STAMP)


class DocumentIntakeTests(unittest.TestCase):
    def test_sorted_ids_immutable_records_and_source(self):
        result = batch([{"id": 2, "value": "B"}, {"id": 1, "value": "A"}])
        self.assertEqual([r.record_id for r in result.records], ["1", "2"])
        self.assertEqual(result.collected_at, "2026-10-22T15:30:00Z")
        self.assertEqual(len(result.source_sha256), 64)
        self.assertEqual(len(result.records[0].sha256), 64)

    def test_same_data_regardless_of_key_order(self):
        a = batch([{"id": 2, "foo": 1, "bar": "x"}])
        b = batch([{"bar": "x", "foo": 1, "id": 2}])
        self.assertEqual(a.records[0].sha256, b.records[0].sha256)

    def test_non_destructive_change_plan(self):
        before = batch([{"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}])
        after = batch([{"id": "b", "v": 1}, {"id": "c", "v": 2}, {"id": "d", "v": 3}])
        changes = diff_records(before, after)
        self.assertEqual([(x.record_id, x.status) for x in changes],
                         [("a", "missing"), ("b", "unchanged"), ("c", "changed"), ("d", "added")])

    def test_cross_source_conflicts(self):
        a = batch([{"id": 1, "name": "first"}])
        b = batch([{"id": 1, "name": "second"}], source="https://example.com/guide")
        self.assertEqual(find_cross_source_conflicts(a, b), ("1",))

    def test_duplicates_and_ambiguous_ids_rejected(self):
        for items in ([{"id": 1}, {"id": "1"}], [{"id": True}], [{"name": "missing"}],
                      [{"id": -1}], [{"id": " a "}]):
            with self.subTest(items=items), self.assertRaises(DocumentIntakeError):
                batch(items)

    def test_invalid_json_and_unbounded_inputs(self):
        for raw in (b"nope", b'{"bad": 2}', b'["row"]', b"[NaN]",
                    b"[" + b" " * (8 * 1024 * 1024) + b"]"):
            with self.subTest(length=len(raw)), self.assertRaises(DocumentIntakeError):
                ingest_json_records(raw, source="local:test", collected_at=STAMP)

    def test_timestamp_and_source_must_be_explicit(self):
        for bad in ("../file", "ftp://example.com", "http://insecure"):
            with self.assertRaises(DocumentIntakeError):
                ingest_json_records(b"[]", source=bad, collected_at=STAMP)
        with self.assertRaises(DocumentIntakeError):
            ingest_json_records(b"[]", source="local:fixture", collected_at="2026-01-01")

    def test_wrapper_and_empty_list(self):
        result = ingest_json_records(b'{"records":[{"id":"q1"}]}',
                                     source="local:fixture", collected_at=STAMP)
        self.assertEqual(result.records[0].record_id, "q1")
        self.assertEqual(len(batch([]).records), 0)


if __name__ == "__main__":
    unittest.main()

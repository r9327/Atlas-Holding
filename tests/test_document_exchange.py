import hashlib
import json
import unittest

from holding.document_exchange import export_document_batch
from holding.document_intake import ingest_json_records

STAMP = "2026-10-22T17:30:00+02:00"


class DocumentExchangeTests(unittest.TestCase):
    def test_exports_versioned_stable_wire_format(self):
        raw = b'[{"id":42,"kind":"quest","label":"hello","references":[]}]'
        batch = ingest_json_records(raw, source="local:fixture", collected_at=STAMP)
        result = export_document_batch(batch)
        obj = json.loads(result)
        self.assertEqual(obj["schema"], "documentalist-batch/v1")
        self.assertEqual(obj["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(obj["records"][0]["id"], "42")
        self.assertEqual(obj["records"][0]["payload"]["kind"], "quest")
        self.assertEqual(obj["records"][0]["sha256"], batch.records[0].sha256)
        self.assertEqual(result, export_document_batch(batch))

    def test_can_export_empty_snapshot_without_deletions(self):
        batch = ingest_json_records(b"[]", source="local:fixture", collected_at=STAMP)
        self.assertEqual(json.loads(export_document_batch(batch))["records"], [])


if __name__ == "__main__":
    unittest.main()

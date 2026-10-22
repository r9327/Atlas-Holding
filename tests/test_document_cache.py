from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from holding.document_cache import DocumentCacheError, load_cached_batch, store_batch
from holding.document_csv import ingest_csv_records
from holding.document_intake import ingest_json_records

SOURCE = "https://example.org/data.json"
STAMP = "2026-10-22T17:20:00Z"


def batch(source=SOURCE, raw=b'[{"id":42,"kind":"quest","name":"Start"}]'):
    return ingest_json_records(raw, source=source, collected_at=STAMP)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "cache"

    def test_verified_roundtrip_immutable_canonical_records(self):
        original = batch()
        digest = store_batch(self.root, original, format="json")
        replay = load_cached_batch(self.root, source=SOURCE, format="json")
        self.assertIsNotNone(replay)
        self.assertEqual(replay.verified_wire_sha256, digest)
        self.assertEqual(replay.batch.source_sha256, original.source_sha256)
        self.assertEqual(replay.batch.records, original.records)

    def test_cache_key_preserves_format_and_identifiers(self):
        csv = ingest_csv_records(b"id,kind\n001,quest\n", source=SOURCE, collected_at=STAMP)
        store_batch(self.root, csv, format="csv")
        same = load_cached_batch(self.root, source=SOURCE, format="csv")
        self.assertEqual(same.batch.records[0].record_id, "001")
        self.assertIsNone(load_cached_batch(self.root, source=SOURCE, format="json"))
        self.assertIsNone(load_cached_batch(self.root, source="https://example.org/other", format="csv"))

    def test_tampered_blob_is_never_reused(self):
        digest = store_batch(self.root, batch(), format="json")
        blob = self.root / "objects" / (digest + ".json")
        blob.write_bytes(b'{"fake":"payload"}')
        with self.assertRaisesRegex(DocumentCacheError, "integrity"):
            load_cached_batch(self.root, source=SOURCE, format="json")

    def test_corrupt_metadata_fails_closed(self):
        store_batch(self.root, batch(), format="json")
        pointer = next((self.root / "entries").glob("*.json"))
        pointer.write_text('{"schema":"wrong"}', encoding="utf-8")
        with self.assertRaises(DocumentCacheError):
            load_cached_batch(self.root, source=SOURCE, format="json")

    def test_entry_quota_prevents_unbounded_growth(self):
        store_batch(self.root, batch(), format="json", max_entries=1)
        with self.assertRaisesRegex(DocumentCacheError, "count exceeded"):
            store_batch(self.root, batch(source="https://example.org/other"), format="json", max_entries=1)

    def test_real_offline_cli_uses_cached_source_without_network(self):
        store_batch(self.root, batch(), format="json")
        out = Path(self.tmp.name) / "result.exchange.json"
        run = subprocess.run([
            sys.executable, "-m", "holding.documentalist_cli",
            "--url", SOURCE, "--format", "json", "--allow-host", "example.org",
            "--cache-dir", str(self.root), "--offline", "--out", str(out),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["source_mode"], "OFFLINE_CACHE")
        self.assertEqual(result["collected_at"], STAMP)
        self.assertEqual(result["source_bytes"], 0)
        self.assertEqual(json.loads(out.read_text())["records"][0]["id"], "42")

    def test_offline_cache_miss_fails_without_network(self):
        output = Path(self.tmp.name) / "missing.exchange.json"
        run = subprocess.run([
            sys.executable, "-m", "holding.documentalist_cli",
            "--url", SOURCE, "--format", "json", "--allow-host", "example.org",
            "--cache-dir", str(self.root), "--offline", "--out", str(output),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 2)
        self.assertIn("OFFLINE_CACHE_MISS", run.stderr)
        self.assertFalse(output.exists())

    def test_offline_refuses_unapproved_domain_even_with_cache(self):
        store_batch(self.root, batch(), format="json")
        output = Path(self.tmp.name) / "blocked.json"
        run = subprocess.run([
            sys.executable, "-m", "holding.documentalist_cli",
            "--url", SOURCE, "--format", "json", "--allow-host", "different.org",
            "--cache-dir", str(self.root), "--offline", "--out", str(output),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 2)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

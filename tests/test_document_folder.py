"""Documentalist folder intake: real filesystem, replay and failure tests."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from holding.document_folder import DocumentFolderError, import_document_folder

STAMP1 = "2026-10-23T18:00:00Z"
STAMP2 = "2026-10-23T19:00:00Z"


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.outputs = self.root / "exports"
        self.inputs.mkdir()

    def write(self, name: str, content: str):
        path = self.inputs / name
        path.write_text(content, encoding="utf-8")
        return path

    def run_batch(self, **kwargs):
        return import_document_folder(
            self.inputs, self.outputs, collected_at=STAMP1, **kwargs,
        )

    def test_four_local_formats_sequentially_and_immutable_exports(self):
        self.write("01.json", '[{"id":42,"kind":"quest"}]')
        self.write("02.csv", "id,kind\n002,success\n")
        self.write("03.md", "| id | kind |\n| --- | --- |\n| 003 | guide |\n")
        self.write("04.html", "<table><tr><th>id</th><th>kind</th></tr>"
                              "<tr><td>004</td><td>npc</td></tr></table>")
        self.write("05.pdf", "this is not a PDF parser")
        summary = self.run_batch()
        self.assertEqual((summary.eligible, summary.created, summary.reused), (4, 4, 0))
        self.assertEqual(summary.scanned, 5)
        self.assertEqual(len(list(self.outputs.glob("*.batch.json"))), 4)
        self.assertEqual(
            json.loads((self.outputs / "03.md.batch.json").read_text())["records"][0]["id"],
            "003",
        )
        self.assertEqual(
            json.loads((self.outputs / "01.json.batch.json").read_text())["source_sha256"],
            __import__("hashlib").sha256((self.inputs / "01.json").read_bytes()).hexdigest(),
        )

    def test_resume_is_idempotent_and_preserves_original_timestamp(self):
        self.write("quest.json", '[{"id":1,"label":"same"}]')
        self.run_batch()
        path = self.outputs / "quest.json.batch.json"
        first = path.read_bytes()
        result = import_document_folder(self.inputs, self.outputs, collected_at=STAMP2)
        self.assertEqual((result.created, result.reused), (0, 1))
        self.assertEqual(first, path.read_bytes())
        self.assertEqual(json.loads(first)["collected_at"], STAMP1)

    def test_changed_source_is_conflict_not_overwrite(self):
        src = self.write("quest.json", '[{"id":1,"label":"first"}]')
        self.run_batch()
        output = self.outputs / "quest.json.batch.json"
        original = output.read_bytes()
        src.write_text('[{"id":1,"label":"changed"}]', encoding="utf-8")
        with self.assertRaisesRegex(DocumentFolderError, "conflicts"):
            self.run_batch()
        self.assertEqual(output.read_bytes(), original)

    def test_tampered_exchange_fails_closed(self):
        self.write("quest.json", '[{"id":1}]')
        self.run_batch()
        saved = self.outputs / "quest.json.batch.json"
        saved.write_text('{"status":"fake"}', encoding="utf-8")
        with self.assertRaises(DocumentFolderError):
            self.run_batch()
        self.assertEqual(saved.read_text(), '{"status":"fake"}')

    def test_quotas_refused_before_processing(self):
        self.write("one.json", '[{"id":1}]')
        self.write("two.json", '[{"id":2}]')
        with self.assertRaisesRegex(DocumentFolderError, "count exceeds"):
            self.run_batch(max_files=1)
        self.assertFalse(self.outputs.exists())
        with self.assertRaisesRegex(DocumentFolderError, "total byte budget"):
            self.run_batch(max_total_bytes=10)
        self.assertEqual(list(self.outputs.glob("*.json")), [])

    def test_invalid_source_leaves_no_output_for_that_document(self):
        self.write("bad.json", '[{"id":1},{"id":1}]')
        with self.assertRaisesRegex(DocumentFolderError, "invalid source"):
            self.run_batch()
        self.assertEqual(list(self.outputs.glob("*.batch.json")), [])

    def test_symlinks_are_refused_without_reading_external_files(self):
        self.write("good.json", '[{"id":1}]')
        link = self.inputs / "escape.json"
        try:
            link.symlink_to(self.root / "outside", target_is_directory=False)
        except (OSError, NotImplementedError):
            self.skipTest("filesystem symlinks unavailable")
        with self.assertRaisesRegex(DocumentFolderError, "symlinks"):
            self.run_batch()
        self.assertFalse(self.outputs.exists())

    def test_case_collision_rejected_for_windows_compatibility(self):
        self.write("GUIDE.json", '[{"id":1}]')
        try:
            self.write("guide.json", '[{"id":2}]')
        except OSError:
            self.skipTest("case-insensitive filesystem")
        if len(list(self.inputs.iterdir())) != 2:
            self.skipTest("case-insensitive filesystem")
        with self.assertRaisesRegex(DocumentFolderError, "case-colliding"):
            self.run_batch()

    def test_more_than_thousand_documents_imported(self):
        for number in range(1001):
            self.write("quest-" + str(number).zfill(4) + ".json", '[{"id":1}]')
        result = self.run_batch()
        self.assertEqual(result.eligible, 1001)
        self.assertEqual(result.created, 1001)
        self.assertEqual(len(list(self.outputs.glob("*.batch.json"))), 1001)

    def test_real_cli_emits_machine_readable_progress_summary(self):
        self.write("data.json", '[{"id":77,"name":"Atlas"}]')
        p = subprocess.run([
            sys.executable, "-m", "holding.document_folder",
            "--input-dir", str(self.inputs),
            "--out-dir", str(self.outputs),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        report = json.loads(p.stdout)
        self.assertEqual((report["created"], report["reused"]), (1, 0))
        p2 = subprocess.run([
            sys.executable, "-m", "holding.document_folder",
            "--input-dir", str(self.inputs),
            "--out-dir", str(self.outputs),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertEqual(json.loads(p2.stdout)["reused"], 1)

    def test_custom_identifier_field_supported(self):
        self.write("quests.json", '[{"quest_id":"q-123","kind":"quest"}]')
        self.run_batch(id_field="quest_id")
        doc = json.loads((self.outputs / "quests.json.batch.json").read_text())
        self.assertEqual(doc["records"][0]["id"], "q-123")

    def test_same_input_output_root_and_invalid_config_rejected(self):
        self.write("quests.json", '[{"id":1}]')
        with self.assertRaisesRegex(DocumentFolderError, "must differ"):
            import_document_folder(self.inputs, self.inputs)
        with self.assertRaises(DocumentFolderError):
            self.run_batch(max_total_bytes=True)


if __name__ == "__main__":
    unittest.main()

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DocumentalistCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "quests.json"
        self.input.write_text(json.dumps([
            {"id": 42, "kind": "quest", "label": "Départ"},
            {"id": 10, "kind": "success", "label": "Objectif"},
        ]), encoding="utf-8")
        self.out = self.root / "exchange.json"

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "holding.documentalist_cli",
             "--file", str(self.input), "--format", "json",
             "--out", str(self.out), *args],
            capture_output=True, text=True, check=False,
        )

    def test_local_json_exports_canonical_exchange(self):
        run = self.run_cli()
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report["status"], "EXPORTED")
        self.assertEqual(report["records"], 2)
        export = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(export["schema"], "documentalist-batch/v1")
        self.assertEqual(export["source"], "local:quests.json")
        self.assertEqual([r["id"] for r in export["records"]], ["10", "42"])
        self.assertEqual(export["records"][1]["payload"]["kind"], "quest")

    def test_existing_exchange_is_never_overwritten(self):
        self.out.write_bytes(b"do-not-overwrite")
        run = self.run_cli()
        self.assertEqual(run.returncode, 2)
        self.assertEqual(self.out.read_bytes(), b"do-not-overwrite")

    def test_reject_duplicate_id_without_writing(self):
        self.input.write_text('[{"id":1},{"id":1}]', encoding="utf-8")
        run = self.run_cli()
        self.assertEqual(run.returncode, 2)
        self.assertFalse(self.out.exists())

    def test_csv_preserves_string_ids(self):
        self.input.write_text("id,kind,label\n002,quest,Depart\n", encoding="utf-8")
        run = subprocess.run(
            [sys.executable, "-m", "holding.documentalist_cli", "--file", str(self.input),
             "--format", "csv", "--out", str(self.out)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(self.out.read_text())["records"][0]["id"], "002")

    def test_html_table_exports_for_atlas(self):
        self.input.write_text(
            "<table><tr><th>id</th><th>kind</th></tr>"
            "<tr><td>002</td><td>quest</td></tr></table>",
            encoding="utf-8",
        )
        run = subprocess.run(
            [sys.executable, "-m", "holding.documentalist_cli", "--file", str(self.input),
             "--format", "html", "--out", str(self.out)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        document = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(document["records"][0]["id"], "002")
        self.assertEqual(document["records"][0]["payload"]["kind"], "quest")

    def test_local_markdown_table_exports_records(self):
        self.input.write_text(
            "| id | kind |\n| --- | --- |\n| 004 | quest |\n",
            encoding="utf-8",
        )
        run = subprocess.run([
            sys.executable, "-m", "holding.documentalist_cli",
            "--file", str(self.input), "--format", "markdown",
            "--out", str(self.out),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(self.out.read_text())["records"][0]["id"], "004")

    def test_reject_host_option_for_local_file(self):
        run = self.run_cli("--allow-host", "example.org")
        self.assertEqual(run.returncode, 2)
        self.assertFalse(self.out.exists())

    def test_missing_source_never_creates_output(self):
        self.input.unlink()
        run = self.run_cli()
        self.assertEqual(run.returncode, 2)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()

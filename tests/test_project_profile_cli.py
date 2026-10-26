import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from holding.project_profile_cli import main


SAMPLE = {
    "schema": "project-profile/v1",
    "name": "python-fixture",
    "repository": "example/fixture",
    "branch": "feat/test",
    "checkout_dir": "fixture",
    "commands": {"test": ["python", "-m", "unittest", "discover"]},
    "adapters": [],
}


class ProjectProfileCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "project.json"
        self.file.write_text(json.dumps(SAMPLE), encoding="utf-8")

    def test_cli_subprocess_accepts_valid_project(self):
        run = subprocess.run(
            [sys.executable, "-m", "holding.project_profile_cli", str(self.file), "--json"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        output = json.loads(run.stdout)
        self.assertEqual(output["status"], "VALID")
        self.assertEqual(output["repository"], "example/fixture")
        self.assertEqual(len(output["sha256"]), 64)
        self.assertEqual(output["stages"], ["test"])

    def test_reject_malformed_manifest_without_execution(self):
        self.file.write_text('{"schema": "bad"}', encoding="utf-8")
        run = subprocess.run(
            [sys.executable, "-m", "holding.project_profile_cli", str(self.file), "--json"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["status"], "INVALID")

    def test_refuses_large_file(self):
        self.file.write_bytes(b" " * 65537)
        self.assertEqual(main([str(self.file), "--json"]), 2)

    def test_rejects_missing_file(self):
        self.assertEqual(main([str(self.file.with_name("missing.json")), "--json"]), 2)


if __name__ == "__main__":
    unittest.main()

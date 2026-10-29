"""F10: exact-commit, bounded import-consumer hints cannot certify dependencies."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
import unittest

from holding.existing_code import inspect_existing_code
from holding.python_import_consumers import (
    ImportConsumerError, inspect_python_import_consumers,
)


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        text=True, capture_output=True, encoding="utf-8",
    ).stdout.strip()


class PythonConsumerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "app").mkdir()
        (self.root / "app" / "feature.py").write_text(
            "class FeatureEngine:\n    pass\n", encoding="utf-8")
        (self.root / "app" / "client.py").write_text(
            "from app.feature import FeatureEngine\n", encoding="utf-8")
        (self.root / "app" / "relative.py").write_text(
            "from .feature import FeatureEngine\n", encoding="utf-8")
        (self.root / "app" / "reexport.py").write_text(
            "from . import feature\n", encoding="utf-8")
        (self.root / "root.py").write_text(
            "import app.feature\n", encoding="utf-8")
        (self.root / "app" / "dynamic.py").write_text(
            'import importlib\nimportlib.import_module("app.feature")\n',
            encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.sha = git(self.root, "rev-parse", "HEAD")

    def read(self, *args):
        return git(self.root, *args)

    def test_imports_are_references_not_proof_of_runtime_use(self):
        report = inspect_existing_code(
            self.read, repository_sha=self.sha,
            acceptance=("Modify FeatureEngine implementation",))
        feature = next(c for c in report.candidates
                       if c.path == "app/feature.py")
        self.assertEqual(
            set(feature.importers),
            {"app/client.py", "app/relative.py", "app/reexport.py", "root.py"})
        self.assertNotIn("app/dynamic.py", feature.importers)
        self.assertEqual(report.consumers_status, "PARTIAL_AST_IMPORT_SCAN")
        self.assertGreaterEqual(report.checked_import_files, 4)
        self.assertLessEqual(report.checked_import_files, 24)
        self.assertFalse(report.can_skip_implementation)

    def test_uncommitted_import_does_not_change_exact_sha(self):
        (self.root / "app" / "client.py").write_text(
            "print('replaced')\n", encoding="utf-8")
        report = inspect_existing_code(
            self.read, repository_sha=self.sha,
            acceptance=("FeatureEngine implementation",))
        feature = next(c for c in report.candidates
                       if c.path == "app/feature.py")
        self.assertIn("app/client.py", feature.importers)

    def test_no_match_remains_not_verified(self):
        report = inspect_existing_code(
            self.read, repository_sha=self.sha,
            acceptance=("Create calendar planner widget",))
        self.assertEqual(report.consumers_status, "NOT_VERIFIED")
        self.assertEqual(report.checked_import_files, 0)

    def test_candidate_not_in_git_source_inventory_fails_closed(self):
        with self.assertRaises(ImportConsumerError):
            inspect_python_import_consumers(
                self.read, repository_sha=self.sha,
                source_paths=("app/client.py",),
                candidate_paths=("app/feature.py",))

    def test_bounded_scan_limits_lookups_without_claiming_completeness(self):
        (self.root / "app" / "client.py").write_text(
            "from app.feature import FeatureEngine\n", encoding="utf-8")
        for i in range(45):
            (self.root / "app" / f"consumer_{i:02d}.py").write_text(
                "import app.feature\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "many sources")
        self.sha = git(self.root, "rev-parse", "HEAD")
        calls = []
        def tracked(*args):
            if args[0] == "show":
                calls.append(args)
            return self.read(*args)
        report = inspect_python_import_consumers(
            tracked, repository_sha=self.sha,
            source_paths=tuple(
                p.as_posix().replace(self.root.as_posix() + "/", "")
                for p in self.root.rglob("*.py")),
            candidate_paths=("app/feature.py",))
        self.assertEqual(report.status, "PARTIAL_AST_IMPORT_SCAN")
        self.assertFalse(report.certifies_completeness)
        self.assertLessEqual(len(calls), 24)
        self.assertLessEqual(len(report.importer_paths["app/feature.py"]), 12)


if __name__ == "__main__":
    unittest.main()

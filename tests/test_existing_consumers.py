"""F10 static consumers: inspect actual imports at one immutable Git SHA."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from holding.existing_consumers import inspect_static_import_consumers


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True, capture_output=True, check=True, encoding="utf-8",
    ).stdout.strip()


class StaticConsumerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        app = self.root / "app"
        app.mkdir()
        (app / "__init__.py").write_text("# Package\n", encoding="utf-8")
        (app / "feature.py").write_text(
            "class FeatureEngine:\n    pass\n", encoding="utf-8")
        (app / "consumer.py").write_text(
            "from app.feature import FeatureEngine\n",
            encoding="utf-8")
        (app / "relative.py").write_text(
            "from . import feature\n", encoding="utf-8")
        (app / "decoy.py").write_text(
            "REFERENCE = 'from app.feature import FeatureEngine'\n",
            encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.sha = git(self.root, "rev-parse", "HEAD")

    def inspect(self):
        return inspect_static_import_consumers(
            lambda *args: git(self.root, *args),
            repository_sha=self.sha,
            candidate_paths=("app/feature.py",))

    def test_only_real_absolute_and_relative_importers_are_reported(self):
        report = self.inspect()
        self.assertEqual(report.repository_sha, self.sha)
        self.assertEqual(report.status, "PARTIAL_STATIC_IMPORT_HINTS")
        self.assertEqual(
            {(x.module_path, x.importer_path) for x in report.imports},
            {("app/feature.py", "app/consumer.py"),
             ("app/feature.py", "app/relative.py")})
        self.assertFalse(report.functionality_verified)
        self.assertFalse(report.consumers_exhaustive)
        self.assertFalse(report.can_skip_implementation)

    def test_worktree_modifications_do_not_change_prior_sha_evidence(self):
        (self.root / "app" / "consumer.py").write_text("import os\n")
        report = self.inspect()
        self.assertIn("app/consumer.py", [x.importer_path for x in report.imports])

    def test_absent_static_matches_never_claim_unused_module(self):
        git(self.root, "rm", "app/consumer.py", "app/relative.py")
        git(self.root, "commit", "-m", "remove importers")
        self.sha = git(self.root, "rev-parse", "HEAD")
        report = self.inspect()
        self.assertEqual(report.status, "NOT_VERIFIED")
        self.assertEqual(report.imports, ())
        self.assertFalse(report.consumers_exhaustive)

    def test_grep_failure_does_not_fabricate_negative_proof(self):
        report = inspect_static_import_consumers(
            lambda *args: (_ for _ in ()).throw(RuntimeError("missing git")),
            repository_sha=self.sha,
            candidate_paths=("app/feature.py",))
        self.assertEqual(report.status, "NOT_VERIFIED")
        self.assertFalse(report.consumers_exhaustive)

    def test_bad_sha_and_unsafe_candidates_rejected(self):
        for path in ("../secret.py", "/outside.py", "x.py\nnext.py", "notpy.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                inspect_static_import_consumers(
                    lambda *_: "", repository_sha=self.sha,
                    candidate_paths=(path,))
        with self.assertRaises(ValueError):
            inspect_static_import_consumers(
                lambda *_: "", repository_sha="not-sha",
                candidate_paths=("app/feature.py",))

    def test_bound_on_candidate_count(self):
        with self.assertRaises(ValueError):
            inspect_static_import_consumers(
                lambda *_: "", repository_sha=self.sha,
                candidate_paths=tuple(f"pkg/mod{i}.py" for i in range(13)))


if __name__ == "__main__":
    unittest.main()

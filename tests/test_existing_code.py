"""F10 offline exact-commit existing-code advisory and safety."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
import unittest

from holding.existing_code import ExistingCodeError, inspect_existing_code


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True,
        check=True, encoding="utf-8",
    ).stdout.strip()


class ExistingCodeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "app").mkdir()
        (self.root / "app" / "feature.py").write_text(
            "class FeatureEngine:\n    pass\n\ndef process_feature():\n    return True\n",
            encoding="utf-8",
        )
        (self.root / "notes.md").write_text("unrelated doc\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "initial")
        self.sha = git(self.root, "rev-parse", "HEAD")

    def report(self, *criteria: str):
        return inspect_existing_code(
            lambda *a: git(self.root, *a),
            repository_sha=self.sha, acceptance=tuple(criteria))

    def test_existing_owner_found_with_symbols_but_never_certified(self):
        result = self.report("Use app/feature.py FeatureEngine, keep functionality")
        self.assertEqual(result.repository_sha, self.sha)
        self.assertEqual(result.state, "CANDIDATES_FOUND")
        self.assertIn("app/feature.py", [x.path for x in result.candidates])
        match = next(x for x in result.candidates if x.path == "app/feature.py")
        self.assertEqual(match.symbols, ("FeatureEngine", "process_feature"))
        self.assertEqual(result.github_pr_status, "NOT_CHECKED")
        self.assertEqual(result.functionality_status, "NOT_VERIFIED")
        self.assertEqual(result.consumers_status, "NOT_VERIFIED")
        self.assertFalse(result.can_skip_implementation)

    def test_camel_case_symbol_matches_existing_snake_case_file(self):
        report = self.report("Use FeatureEngine for the new capability")
        self.assertEqual(report.state, "CANDIDATES_FOUND")
        self.assertIn("app/feature.py", [x.path for x in report.candidates])

    def test_no_match_never_claims_absence(self):
        result = self.report("Need calendar scheduling widget")
        self.assertEqual(result.state, "NO_PATH_MATCH_IN_BOUNDED_SCAN")
        self.assertEqual(result.github_pr_status, "NOT_CHECKED")
        self.assertFalse(result.can_skip_implementation)

    def test_large_asset_repository_does_not_require_full_git_tree(self):
        # A Dof-Atlas-sized data tree can contain tens of thousands of images.
        # Inventory only source-like top-level trees; never ask Git to expand data.
        events = []
        object_sha = "b" * 40
        root = (f"040000 tree {object_sha}\tapp\0"
                f"040000 tree {object_sha}\tdata\0"
                f"100644 blob {object_sha}\tREADME.md\0")
        source = f"100644 blob {object_sha}\tapp/feature.py\0"
        def read_git(*args):
            events.append(args)
            if args == ("ls-tree", "-z", self.sha):
                return root
            if args == ("ls-tree", "-r", "-z", self.sha, "--", "app"):
                return source
            if args == ("show", f"{self.sha}:app/feature.py"):
                return "class FeatureEngine:\n    pass\n"
            raise AssertionError("Unexpected broad or data inventory request")
        report = inspect_existing_code(
            read_git, repository_sha=self.sha,
            acceptance=("feature",))
        self.assertEqual(report.state, "CANDIDATES_FOUND")
        self.assertEqual(report.candidates[0].symbols, ("FeatureEngine",))
        self.assertNotIn(("ls-tree", "-r", "-z", self.sha), events)
        self.assertFalse(any("data" in a for args in events for a in args))

    def test_many_source_roots_fail_closed_instead_of_truncating(self):
        object_sha = "b" * 40
        roots = "".join(
            f"040000 tree {object_sha}\tpackage{i}\0"
            for i in range(65))
        with self.assertRaisesRegex(ExistingCodeError, "Too many Git source"):
            inspect_existing_code(
                lambda *_: roots, repository_sha=self.sha,
                acceptance=("feature",))

    def test_latest_worktree_change_not_silently_mixed_with_base(self):
        (self.root / "app" / "feature.py").write_text(
            "class Replaced:\n    pass\n", encoding="utf-8")
        report = self.report("feature")
        self.assertEqual(report.candidates[0].symbols,
                         ("FeatureEngine", "process_feature"))

    def test_symlink_paths_never_become_source_candidates(self):
        link = self.root / "app" / "another_feature.py"
        try:
            link.symlink_to(self.root / "notes.md")
        except (OSError, NotImplementedError):
            self.skipTest("Symlink unavailable on platform")
        git(self.root, "add", "app/another_feature.py")
        git(self.root, "commit", "-m", "symlink")
        self.sha = git(self.root, "rev-parse", "HEAD")
        result = self.report("feature")
        self.assertNotIn("app/another_feature.py",
                         [x.path for x in result.candidates])

    def test_untrusted_git_inventory_is_refused(self):
        for wrong in ("", "malformed", "100644 blob " + "a"*40 + "\tfoo.py\0" * 2,
                      "120000 blob " + "a"*40 + "\tlink.py\0bad"):
            with self.subTest(wrong=wrong[:30]):
                with self.assertRaises(ExistingCodeError):
                    inspect_existing_code(
                        lambda *_: wrong, repository_sha=self.sha,
                        acceptance=("feature",))

    def test_git_failure_and_invalid_sha_fail_closed(self):
        with self.assertRaises(ExistingCodeError):
            inspect_existing_code(
                lambda *_: "", repository_sha="abc", acceptance=("feature",))
        with self.assertRaises(ExistingCodeError):
            inspect_existing_code(
                lambda *_: (_ for _ in ()).throw(RuntimeError("git failed")),
                repository_sha=self.sha, acceptance=("feature",))

    def test_missing_or_oversized_acceptance_rejected(self):
        for phrases in ((), ("",), ("x" * 1300,), ("a",) * 31):
            with self.subTest(phrases=phrases[:1]), self.assertRaises(ExistingCodeError):
                self.report(*phrases)


if __name__ == "__main__":
    unittest.main()

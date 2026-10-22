"""Real Git regression tests for supervisor-owned Atlas AI Context metadata."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from holding.atlas_context_index import (
    AtlasContextIndexError, render_atlas_context_index,
)
from holding.git_worktree import GitSafetyError, GitWorktrees


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True,
        text=True, check=True,
    ).stdout.strip()


class TrustedAtlasContextIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name, "source")
        self.root.mkdir()
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test Runner")
        git(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "README.md").write_text("source\n", encoding="utf-8")
        (self.root / ".ai").mkdir()
        (self.root / ".ai/context-map.yaml").write_text(
            "schema_version: 2\n", encoding="utf-8")
        (self.root / ".ai/context_index.json").write_text(
            "{}\n", encoding="utf-8")
        git(self.root, "add", "--", "README.md",
            ".ai/context-map.yaml", ".ai/context_index.json")
        git(self.root, "commit", "-m", "fixture: original Atlas source")
        git(self.root, "remote", "add", "origin",
            "https://github.com/r9327/Dof-Atlas.git")
        self.original_sha = git(self.root, "rev-parse", "HEAD")
        self.manager = GitWorktrees(self.root, Path(self.temp.name, "isolated"),
                                    "r9327/Dof-Atlas")

    def test_serializer_matches_atlas_canonical_v2_top_level_tree(self):
        sha = "a" * 40
        source = (
            f"100644 blob {sha}\tREADME.md\n"
            f"040000 tree {'b' * 40}\t.ai\n"
        )
        payload = json.loads(render_atlas_context_index(source))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["source"], "git-top-level-fingerprints")
        self.assertEqual(payload["excluded_top_level"], [".ai"])
        self.assertEqual(payload["entries"],
                         {"README.md": {"mode": "100644", "type": "blob",
                                        "sha": sha}})

    def test_serializer_rejects_duplicate_or_malformed_git_tree(self):
        sha = "a" * 40
        line = f"100644 blob {sha}\tREADME.md\n"
        for bad in (line + line, "garbage", f"100644 blob invalid\tREADME.md",
                    f"100644 blob {sha}\tbad\x00path",
                    "q" * (4 * 1024 * 1024 + 1)):
            with self.subTest(bad=bad[:30]), self.assertRaises(AtlasContextIndexError):
                render_atlas_context_index(bad)

    def test_large_atlas_worktree_uses_explicit_bounded_setup_budget(self):
        with patch.object(self.manager, "_git", wraps=self.manager._git) as calls:
            self.manager.create("large-atlas")
        worktree_add = [
            call for call in calls.call_args_list
            if len(call.args) >= 2 and call.args[:2] == ("worktree", "add")
        ]
        self.assertEqual(len(worktree_add), 1)
        self.assertEqual(worktree_add[0].kwargs["timeout"], 180)

    def test_real_commit_syncs_index_without_executing_atlas_hooks(self):
        tree = self.manager.create("context")
        (tree.path / "feature.py").write_text("FLAG = True\n", encoding="utf-8")
        sha = self.manager.commit("context", ("feature.py",),
                                  "feat: one safe change")
        self.assertNotEqual(sha, self.original_sha)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"),
                         self.original_sha)
        committed = set(git(tree.path, "show", "--pretty=format:",
                            "--name-only", "HEAD").splitlines())
        self.assertEqual(committed, {"feature.py", ".ai/context_index.json"})
        data = json.loads((tree.path / ".ai/context_index.json").read_text(
            encoding="utf-8"))
        self.assertEqual(data["entries"]["feature.py"]["sha"],
                         git(tree.path, "hash-object", "feature.py"))
        self.assertEqual(data["entries"]["README.md"]["sha"],
                         git(tree.path, "hash-object", "README.md"))
        self.assertNotIn(".ai", data["entries"])
        self.assertEqual(
            json.loads(git(tree.path, "show", "HEAD:.ai/context_index.json")),
            data,
        )
        self.assertEqual(
            (self.root / ".ai/context_index.json").read_text(encoding="utf-8"),
            "{}\n",
        )

    def test_supervisor_only_index_cannot_be_passed_as_agent_path(self):
        self.manager.create("forbidden")
        with self.assertRaisesRegex(GitSafetyError, "supervisor-owned"):
            self.manager.commit("forbidden", (".ai/context_index.json",),
                                "feat: fake context index")

    def test_canonical_context_symlink_is_never_followed(self):
        tree = self.manager.create("symlink")
        private = Path(self.temp.name, "sensitive.json")
        private.write_text("never overwrite me", encoding="utf-8")
        index = tree.path / ".ai/context_index.json"
        index.unlink()
        try:
            index.symlink_to(private)
        except (OSError, NotImplementedError):
            self.skipTest("Platform forbids unprivileged symlinks")
        (tree.path / "feature.py").write_text("TEST = 1\n", encoding="utf-8")
        with self.assertRaises(GitSafetyError):
            self.manager.commit("symlink", ("feature.py",),
                                "feat: unsafe symlink test")
        self.assertEqual(private.read_text(encoding="utf-8"),
                         "never overwrite me")

    def test_deleted_canonical_scope_map_does_not_disable_index_guard(self):
        tree = self.manager.create("lost-scope")
        (tree.path / ".ai/context-map.yaml").unlink()
        (tree.path / "feature.py").write_text("TEST = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(GitSafetyError, "metadata"):
            self.manager.commit("lost-scope", ("feature.py",),
                                "feat: scope cannot silently disappear")

    def test_missing_tracked_index_in_real_atlas_blocks_commit(self):
        tree = self.manager.create("missing-index")
        (tree.path / ".ai/context_index.json").unlink()
        (tree.path / "feature.py").write_text("TEST = 1\n", encoding="utf-8")
        with self.assertRaises(GitSafetyError):
            self.manager.commit("missing-index", ("feature.py",),
                                "feat: invalid Atlas metadata")


if __name__ == "__main__":
    unittest.main()

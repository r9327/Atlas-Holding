"""Real Git tests: never touch GitHub, test against local bare remotes."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from holding.self_git_sync import HoldingGitSync, SyncBlocked, _origin_identity


def git(path: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(path), *args], check=True,
                          capture_output=True, text=True)
    return proc.stdout.strip()


class HoldingGitSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.remote = self.dir / "remote.git"
        self.local = self.dir / "local"
        git(self.dir, "init", "--bare", "-q", "--initial-branch=main", str(self.remote))
        git(self.dir, "clone", "-q", str(self.remote), str(self.local))
        for key, value in (("user.email", "example@example.org"), ("user.name", "Test")):
            git(self.local, "config", key, value)
        self.commit(self.local, "hello")
        git(self.local, "push", "-q", "origin", "main")
        self.client = HoldingGitSync(self.local, expected_origin=str(self.remote))

    def commit(self, repo: Path, text: str) -> None:
        file = repo / "project.txt"
        file.write_text(text, encoding="utf-8")
        git(repo, "add", "project.txt")
        git(repo, "commit", "-qm", text)

    def feature(self):
        git(self.local, "switch", "-q", "-c", "feat/test")

    def other_clone(self) -> Path:
        other = self.dir / "other"
        git(self.dir, "clone", "-q", str(self.remote), str(other))
        git(other, "config", "user.name", "Other")
        git(other, "config", "user.email", "other@example.org")
        return other

    def test_recognizes_only_canonical_github_origins(self):
        for url in ("https://github.com/r9327/Atlas-Holding.git",
                    "git@github.com:r9327/Atlas-Holding.git",
                    "ssh://git@github.com/r9327/Atlas-Holding"):
            self.assertEqual(_origin_identity(url), "r9327/Atlas-Holding")
        for url in ("https://evil.com/r9327/Atlas-Holding",
                    "https://github.com/r9327/Dof-Atlas",
                    "https://token@github.com/r9327/Atlas-Holding",
                    "file:///tmp/Atlas-Holding",
                    "https://github.com:invalid/r9327/Atlas-Holding"):
            self.assertIsNone(_origin_identity(url))

    def test_status_is_offline_and_reports_branch_head(self):
        state = self.client.status()
        self.assertEqual(state.branch, "main")
        self.assertEqual(state.head, git(self.local, "rev-parse", "HEAD"))
        self.assertFalse(state.dirty)
        self.assertFalse(state.remote_checked)

    def test_noop_sync(self):
        result = self.client.run("sync")
        self.assertEqual(result.action, "UP_TO_DATE")
        self.assertEqual(result.after.head, result.after.remote_head)

    def test_feature_push_without_force_and_idempotent(self):
        self.feature()
        self.commit(self.local, "feature")
        first = self.client.run("sync")
        self.assertEqual(first.action, "PUSHED")
        self.assertEqual(first.before.remote_head, None)
        self.assertEqual(self.client.run("sync").action, "UP_TO_DATE")
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/feat/test"),
                         git(self.local, "rev-parse", "HEAD"))

    def test_remote_ahead_is_fast_forward_pulled(self):
        other = self.other_clone()
        self.commit(other, "new remote")
        git(other, "push", "-q", "origin", "main")
        self.assertEqual(self.client.run("sync").action, "PULLED")
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), git(other, "rev-parse", "HEAD"))

    def test_push_main_forbidden(self):
        self.commit(self.local, "unauthorized main")
        head_before = git(self.remote, "rev-parse", "main")
        with self.assertRaisesRegex(SyncBlocked, "Direct push"):
            self.client.run("push")
        self.assertEqual(git(self.remote, "rev-parse", "main"), head_before)

    def test_dirty_tree_blocks_without_mutation(self):
        (self.local / "local-extra.txt").write_text("unstaged")
        with self.assertRaisesRegex(SyncBlocked, "Working tree"):
            self.client.run("sync")
        self.assertEqual(git(self.remote, "rev-parse", "main"),
                         git(self.local, "rev-parse", "HEAD"))

    def test_divergence_never_rebases_or_resets(self):
        other = self.other_clone()
        self.commit(self.local, "local divergent")
        self.commit(other, "remote divergent")
        git(other, "push", "-q", "origin", "main")
        local_sha = git(self.local, "rev-parse", "HEAD")
        with self.assertRaisesRegex(SyncBlocked, "diverged"):
            self.client.run("sync")
        self.assertEqual(git(self.local, "rev-parse", "HEAD"), local_sha)

    def test_remote_ahead_cannot_be_overwritten_by_push(self):
        self.feature()
        self.commit(self.local, "feature")
        git(self.local, "push", "-q", "origin", "feat/test")
        other = self.dir / "other"
        git(self.dir, "clone", "-q", "--branch", "feat/test", str(self.remote), str(other))
        git(other, "config", "user.name", "Other")
        git(other, "config", "user.email", "other@example.org")
        self.commit(other, "remote ahead")
        git(other, "push", "-q", "origin", "feat/test")
        with self.assertRaisesRegex(SyncBlocked, "Remote is ahead"):
            self.client.run("push")

    def test_wrong_remote_is_blocked_before_any_fetch(self):
        client = HoldingGitSync(self.local)
        with self.assertRaisesRegex(SyncBlocked, "Origin"):
            client.run("sync")

    def test_detached_head_is_blocked(self):
        git(self.local, "checkout", "-q", "--detach")
        with self.assertRaises(SyncBlocked):
            self.client.status()

    def test_no_upstream_branch_pull_blocks(self):
        self.feature()
        with self.assertRaisesRegex(SyncBlocked, "No remote branch"):
            self.client.run("pull")

    def test_even_noop_push_main_is_forbidden(self):
        with self.assertRaisesRegex(SyncBlocked, "Direct push"):
            self.client.run("push")


if __name__ == "__main__":
    unittest.main()

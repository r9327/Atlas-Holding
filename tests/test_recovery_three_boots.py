"""Durable restart acceptance: real SQLite, Git commits and daemon leases.

These tests kill and replace the service object three times. They are NOT an
operating-system reboot, QA proof, real agent run, or signed release evidence.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from holding.autonomy import Mission, StandingGrant
from holding.daemon_lease import DaemonLease
from holding.executor import AuthorizedGit
from holding.git_worktree import GitWorktrees
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], text=True,
        check=True, capture_output=True, timeout=30,
    ).stdout.strip()


class NeverRunInterruptedWork:
    def ready(self) -> bool:
        return True

    def __call__(self, mission: Mission) -> str:
        raise AssertionError("Restart must not replay prior Git writes")


class ThreeBootRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        canonical = self.root / "canonical"
        canonical.mkdir()
        git(canonical, "init", "-b", "main")
        git(canonical, "config", "user.name", "Restart Test")
        git(canonical, "config", "user.email", "restart@example.invalid")
        (canonical / "README.md").write_text("base\n", encoding="utf-8")
        git(canonical, "add", "README.md")
        git(canonical, "commit", "-m", "initial")
        git(canonical, "remote", "add", "origin",
            "https://github.com/r9327/Dof-Atlas.git")
        self.manager = GitWorktrees(canonical, self.root / "worktrees", REPO)
        self.database = self.root / "holding.sqlite"

    def prepare_interrupted(self, *, journaled_commit: bool) -> tuple[Path, str]:
        with StateStore(self.database) as store:
            store.add_missions([Mission("mission", REPO)])
            store.grant_for_onboarding(StandingGrant(
                REPO, frozenset({"code", "commit"}), int(time.time()) + 360))
            with DaemonLease(self.database, heartbeat=False) as lease:
                self.assertEqual(
                    store.claim_next(online=False, lease_owner=lease.owner).mission_id,
                    "mission",
                )
                actor = AuthorizedGit(store, self.manager, lease)
                work = actor.create("mission")
                (work.path / "code.py").write_text(
                    "print('persisted')\n", encoding="utf-8")
                if journaled_commit:
                    sha = actor.commit("mission", ("code.py",), "feat: durable")
                else:
                    git(work.path, "add", "code.py")
                    git(work.path, "commit", "-m", "external unjournaled write")
                    sha = git(work.path, "rev-parse", "HEAD")
        return work.path, sha

    def assert_three_restarts(self, *, journaled_commit: bool,
                              expected_state: str) -> None:
        work, sha = self.prepare_interrupted(journaled_commit=journaled_commit)
        original_count = git(work, "rev-list", "--count", "HEAD")
        for attempt in range(3):
            with HoldingService(
                ServiceSettings(self.database, idle_seconds=0.01),
                NeverRunInterruptedWork(), online=lambda: False,
                recovery_git=self.manager,
            ) as host:
                quarantined = host.start()
                self.assertEqual(quarantined, ("mission",) if attempt == 0 else ())
                self.assertEqual(len(host.last_recovery),
                                 1 if (attempt == 0 or not journaled_commit) else 0)
                if host.last_recovery:
                    self.assertEqual(host.last_recovery[0].state, expected_state)
                mission = host.engine.store.missions()[0]
                self.assertEqual(mission.state, expected_state)
                self.assertEqual(mission.evidence_sha, "")
                self.assertEqual(mission.failures_without_progress, 0)
                self.assertEqual(mission.candidate_sha,
                                 sha if journaled_commit else "")
                self.assertEqual(host.run(max_ticks=1).action, "WAIT")
                self.assertEqual(git(work, "rev-parse", "HEAD"), sha)
                self.assertEqual(git(work, "rev-list", "--count", "HEAD"),
                                 original_count)
                self.assertEqual(
                    host.engine.store.db.execute(
                        "SELECT COUNT(*) FROM git_operations"
                        " WHERE mission_id='mission'"
                    ).fetchone()[0],
                    2 if journaled_commit else 1,
                )

    def test_journaled_commit_survives_three_service_restarts_without_replay(self):
        self.assert_three_restarts(
            journaled_commit=True, expected_state="QA_PENDING")

    def test_clean_unjournaled_commit_stays_quarantined_after_three_restarts(self):
        self.assert_three_restarts(
            journaled_commit=False, expected_state="RECOVERY_REQUIRED")


if __name__ == "__main__":
    unittest.main()

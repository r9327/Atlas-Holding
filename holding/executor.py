"""Authorized Git operations for a claimed mission (not an LLM command tool)."""
from __future__ import annotations

import time
from .git_worktree import GitWorktrees, Worktree, GitSafetyError
from .daemon_lease import DaemonLease
from .state_store import StateStore


class AuthorizedGit:
    """Rechecks both mission state and standing grants immediately before Git.

    This class is ONLY to be used by the trusted daemon. It has no generic
    execute(command) entry point and cannot force push or write main.
    """

    def __init__(self, store: StateStore, git: GitWorktrees, lease: DaemonLease):
        self.store = store
        self.git = git
        self.lease = lease

    def _require(self, mission_id: str, *operations: str) -> None:
        self.lease.assert_owned()
        rows = [job for job in self.store.missions() if job.id == mission_id]
        if len(rows) != 1 or rows[0].state != 'RUNNING':
            raise GitSafetyError('A claimed RUNNING mission is required')
        job = rows[0]
        if job.repo != self.git.repository:
            raise GitSafetyError('Mission repository differs from canonical Git repository')
        if self.store._paused():
            raise GitSafetyError('Execution paused')
        now = int(time.time())
        grants = self.store._grants()
        for op in operations:
            if not any(grant.allows(job.repo, op, now) for grant in grants):
                raise GitSafetyError(f'No active {op} permission for exact repository')

    def create(self, mission_id: str) -> Worktree:
        self._require(mission_id, 'code')
        tree = self.git.create(mission_id)
        self.lease.assert_owned()
        return tree

    def commit(self, mission_id: str, paths: tuple[str, ...], message: str) -> str:
        self._require(mission_id, 'code', 'commit')
        sha = self.git.commit(mission_id, paths, message)
        self.lease.assert_owned()
        return sha

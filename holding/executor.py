"""Authorized Git operations for a claimed mission (not an LLM command tool)."""
from __future__ import annotations

import time
from .git_journal import GitJournal
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
        self.journal = GitJournal(store, git, lease_owner=lease.owner)

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
        key = 'worktree-create-v1'
        self.git.inspect_canonical()
        base = self.git._git('rev-parse', '--verify', 'refs/heads/main^{commit}')
        old = self.journal.get(mission_id, key)
        if old:
            if old.status == 'APPLIED' and old.base_sha == base:
                return self.git.inspect(mission_id)
            raise GitSafetyError('Unfinished worktree creation requires recovery inspection')
        self.journal.prepare(mission_id, key, 'CREATE',
                             intent={'base': 'main'}, base_sha=base)
        tree = self.git.create(mission_id)
        self.lease.assert_owned()
        if tree.base_sha != base:
            raise GitSafetyError('Created worktree at unexpected base SHA')
        self.journal.applied(mission_id, key, base)
        return tree

    def commit(self, mission_id: str, paths: tuple[str, ...], message: str,
               *, operation_key: str | None = None) -> str:
        self._require(mission_id, 'code', 'commit')
        if not paths or not message.strip():
            raise GitSafetyError('Explicit code paths and commit message required')
        intent = {'paths': list(paths), 'message': message}
        key = operation_key or 'commit-' + GitJournal.digest(intent)[:32]
        old = self.journal.get(mission_id, key)
        if old:
            if old.kind != 'COMMIT' or old.intent_digest != GitJournal.digest(intent):
                raise GitSafetyError('Operation key reused with different commit intent')
            if old.status == 'APPLIED':
                if self.journal.inspect_recovery(mission_id, key) != 'APPLIED':
                    raise GitSafetyError('Previously applied commit is no longer in local history')
                return old.result_sha
            raise GitSafetyError('Unresolved commit intent: inspect recovery before retry')
        base = self.git.inspect(mission_id).base_sha
        self.journal.prepare(mission_id, key, 'COMMIT', intent=intent, base_sha=base)
        # This trailer is a recovery marker, NOT authentication by itself.
        result = self.git.commit(mission_id, paths, message + '\n\nHolding-Operation: ' + key)
        self.lease.assert_owned()
        if self.journal.inspect_recovery(mission_id, key) != 'APPLIED':
            raise GitSafetyError('Commit history does not match recorded intent')
        return result

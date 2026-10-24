"""Reconcile interrupted Git missions without repeating writes or inventing PASS.

A clean worktree is not enough to prove a commit. Journaled operations and the
actual Git ancestry must agree, and the daemon lease must be current.
"""
from __future__ import annotations

from dataclasses import dataclass

from .daemon_lease import DaemonLease
from .git_journal import GitJournal
from .git_worktree import GitSafetyError, GitWorktrees
from .state_store import StateStore, StoreError


@dataclass(frozen=True)
class RecoveryOutcome:
    mission_id: str
    state: str
    reason: str


class RecoveryCoordinator:
    """Trusted supervisor helper; never grant it directly to the coding LLM."""

    def __init__(self, store: StateStore, git: GitWorktrees, lease: DaemonLease):
        self.store = store
        self.git = git
        self.lease = lease
        self.journal = GitJournal(store, git, lease_owner=lease.owner)

    def recover(self, mission_id: str) -> RecoveryOutcome:
        self.lease.assert_owned()
        jobs = {mission.id: mission for mission in self.store.missions()}
        mission = jobs.get(mission_id)
        if mission is None or mission.repo != self.git.repository:
            raise StoreError("Unknown or foreign recovery mission")
        if mission.state != "RECOVERY_REQUIRED":
            raise StoreError("Only quarantined missions may be reconciled")
        operations = self.store.db.execute(
            "SELECT operation_key,kind FROM git_operations WHERE mission_id=?",
            (mission_id,),
        ).fetchall()
        if not operations:
            return RecoveryOutcome(mission_id, "RECOVERY_REQUIRED", "No auditable Git journal")
        # An ambiguous or incomplete write remains quarantined. Other unrelated
        # missions may continue; this recovery does not block the whole queue.
        for row in operations:
            result = self.journal.inspect_recovery(mission_id, row["operation_key"])
            if result != "APPLIED":
                return RecoveryOutcome(mission_id, "RECOVERY_REQUIRED",
                                       "Git operation requires independent inspection")
        if not self.git.inspect_recovery(mission_id):
            return RecoveryOutcome(mission_id, "RECOVERY_REQUIRED", "Dirty or missing Git worktree")
        head = self.git.inspect(mission_id).base_sha
        commits = self.store.db.execute(
            "SELECT result_sha FROM git_operations WHERE mission_id=? AND kind='COMMIT' AND state='APPLIED'",
            (mission_id,),
        ).fetchall()
        if commits:
            if head not in {row["result_sha"] for row in commits}:
                return RecoveryOutcome(mission_id, "RECOVERY_REQUIRED",
                                       "HEAD does not match a verified commit")
            self.store.recover_verified_commit(mission_id, head, lease_owner=self.lease.owner)
            return RecoveryOutcome(mission_id, "QA_PENDING", "Verified commit awaiting Doctor")
        # Worktree creation alone is a safe, replayable checkpoint once clean.
        self.store.acknowledge_recovery(mission_id, safe_worktree_confirmed=True,
                                        lease_owner=self.lease.owner)
        return RecoveryOutcome(mission_id, "READY", "Clean worktree ready to continue")

    def recover_all(self) -> tuple[RecoveryOutcome, ...]:
        """One damaged mission must not prevent unrelated jobs from resuming."""
        outcomes = []
        for mission in self.store.missions():
            if mission.state != "RECOVERY_REQUIRED" or mission.repo != self.git.repository:
                continue
            try:
                outcomes.append(self.recover(mission.id))
            except (StoreError, GitSafetyError, ValueError) as error:
                # Never mask a lost daemon lease; old daemons cannot declare
                # other missions recoverable after another instance took over.
                self.lease.assert_owned()
                with self.store._transaction():
                    self.store._assert_daemon_fence(self.lease.owner)
                    self.store._event(mission.id, "RECOVERY_INSPECTION_ERROR", type(error).__name__)
                outcomes.append(RecoveryOutcome(mission.id, "RECOVERY_REQUIRED",
                                                "Recovery inspection failed; quarantined"))
        return tuple(outcomes)

"""Trusted mission adapter for journaled external synchronization effects."""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable

from .autonomy import Mission
from .daemon_lease import DaemonLease
from .engine import WorkerBlocked, WorkerDeferred, WorkerInspectionRequired
from .git_push import GitRemotePushControl, coordinate_git_push
from .git_worktree import GitSafetyError, GitWorktrees
from .state_store import StateStore, StoreError


_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")


@dataclass(frozen=True)
class VerifiedGitHubSync:
    sync_operation: str
    branch: str
    candidate_sha: str
    idempotency_key: str


class GitPushMissionWorker:
    """Resolve a certified dependency and execute its exact non-force push."""

    def __init__(
        self, store: StateStore, git: GitWorktrees, lease: DaemonLease, *,
        expected_account_id: str, expected_policy_sha: str,
        clock: Callable[[], float] = time.time,
        control_factory: Callable[[str], object] | None = None,
    ):
        if (not _ACCOUNT.fullmatch(expected_account_id)
                or not re.fullmatch(r"[0-9a-f]{40}", expected_policy_sha)
                or not callable(clock)):
            raise ValueError("Invalid trusted GitHub worker configuration")
        self.store = store
        self.git = git
        self.lease = lease
        self.account = expected_account_id
        self.policy = expected_policy_sha
        self.clock = clock
        self.control_factory = control_factory or (
            lambda mission_id: GitRemotePushControl(git, mission_id))

    def ready(self) -> bool:
        try:
            self.git.inspect_canonical()
            return True
        except Exception:
            return False

    def _source(self, mission: Mission) -> tuple[str, str, str]:
        if (not isinstance(mission, Mission) or mission.operation != "push"
                or mission.repo != self.git.repository or mission.state != "RUNNING"):
            raise WorkerBlocked("PUSH_MISSION_IDENTITY_INVALID")
        jobs = {job.id: job for job in self.store.missions()}
        candidates = []
        for dependency_id in mission.dependencies:
            dependency = jobs.get(dependency_id)
            if (dependency is None or dependency.state != "DONE"
                    or dependency.candidate_sha != dependency.evidence_sha
                    or not _SHA.fullmatch(dependency.candidate_sha)):
                raise WorkerBlocked("PUSH_DEPENDENCY_NOT_CERTIFIED")
            try:
                tree = self.git.inspect(dependency_id)
            except GitSafetyError:
                continue
            if tree.base_sha == dependency.candidate_sha:
                candidates.append((dependency_id, tree.branch, tree.base_sha))
        if len(candidates) != 1:
            raise WorkerBlocked("PUSH_SOURCE_WORKTREE_AMBIGUOUS")
        return candidates[0]

    def __call__(self, mission: Mission) -> VerifiedGitHubSync:
        source_id, branch, candidate_sha = self._source(mission)
        now_epoch = int(self.clock())
        try:
            receipt, digest = self.store.github_authorization_receipt(
                repository=mission.repo, operation="github.push",
                branch=branch, candidate_sha=candidate_sha,
                expected_account_id=self.account,
                expected_policy_sha=self.policy, now_epoch=now_epoch)
        except StoreError as exc:
            raise WorkerBlocked("PUSH_AUTHORIZATION_INVALID") from exc
        result = coordinate_git_push(
            self.store, self.control_factory(source_id), receipt,
            expected_receipt_sha256=digest,
            expected_account_id=self.account,
            expected_policy_sha=self.policy, repository=mission.repo,
            branch=branch, candidate_sha=candidate_sha,
            now_epoch=now_epoch, lease_owner=self.lease.owner)
        if result.status in {"SUCCEEDED", "ALREADY_DONE"}:
            if not _DIGEST.fullmatch(result.idempotency_key):
                raise WorkerBlocked("PUSH_RESULT_INVALID")
            return VerifiedGitHubSync(
                "github.push", branch, candidate_sha,
                result.idempotency_key)
        if result.status in {"GITHUB_HEALTH_BLOCKED", "RETRY", "WAIT"}:
            raise WorkerDeferred(result.status)
        if result.status == "INSPECT_REQUIRED":
            raise WorkerInspectionRequired("PUSH_OUTCOME_REQUIRES_INSPECTION")
        raise WorkerBlocked(result.status)

"""Deterministic, fail-closed unattended task selection.

Selects *intent* only. Does not execute commands or authenticate Doctor proofs:
the eventual executor independently checks the grant, SHA and worktree.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

FORBIDDEN_OPERATIONS = frozenset({
    "force_push", "change_security_policy", "disable_doctor",
    "disable_ci", "delete_user_data", "modify_secrets", "spend_money",
    "publish_private_data", "write_main",
})
KNOWN_OPERATIONS = frozenset({
    "plan", "code", "local_test", "commit", "push", "open_pr",
    "ready_for_review", "read_ci", "merge",
})
TERMINAL = frozenset({"DONE", "CANCELLED"})


@dataclass(frozen=True)
class StandingGrant:
    repository: str
    operations: frozenset[str]
    expires_epoch: int
    enabled: bool = True

    def allows(self, repo: str, operation: str, now_epoch: int) -> bool:
        return (
            self.enabled
            and self.repository == repo
            and now_epoch < self.expires_epoch
            and operation in KNOWN_OPERATIONS
            and operation not in FORBIDDEN_OPERATIONS
            and operation in self.operations
        )


@dataclass(frozen=True)
class Mission:
    id: str
    repo: str
    operation: str = "code"
    dependencies: tuple[str, ...] = ()
    priority: int = 0
    state: str = "READY"
    candidate_sha: str = ""
    evidence_sha: str = ""
    external_required: bool = False
    product_decision_required: bool = False
    failures_without_progress: int = 0


@dataclass(frozen=True)
class Decision:
    action: str
    mission_id: str | None
    reason: str


def choose_next(
    missions: Iterable[Mission], grants: Iterable[StandingGrant],
    *, now_epoch: int, online: bool, retry_limit: int = 3,
) -> Decision:
    """Select independent, authorized work without requesting routine clicks."""
    if retry_limit < 1:
        raise ValueError("retry_limit must be positive")
    jobs = tuple(missions)
    ids = [job.id for job in jobs]
    if len(set(ids)) != len(ids) or any(not name for name in ids):
        return Decision("BLOCKED", None, "Duplicate or blank task IDs")
    by_id = {job.id: job for job in jobs}
    if any(not job.repo for job in jobs):
        return Decision("BLOCKED", None, "Task missing exact repository")
    for job in jobs:
        if any(dep not in by_id for dep in job.dependencies):
            return Decision("BLOCKED", job.id, "Unknown dependency")
        if job.state == "DONE" and (
            not job.candidate_sha or job.candidate_sha != job.evidence_sha
        ):
            return Decision("BLOCKED", job.id, "Missing or stale completion evidence")

    # A complete user roadmap can contain thousands of chained missions.
    # Explicit DFS frames avoid Python's recursion limit (and a daemon crash).
    visiting: set[str] = set()
    visited: set[str] = set()
    for job in jobs:
        if job.id in visited:
            continue
        stack: list[tuple[str, bool]] = [(job.id, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                visiting.remove(node)
                visited.add(node)
                continue
            if node in visited:
                continue
            if node in visiting:
                return Decision("BLOCKED", None, "Dependency cycle")
            visiting.add(node)
            stack.append((node, True))
            stack.extend((dep, False) for dep in reversed(by_id[node].dependencies))
    if not jobs or all(job.state in TERMINAL for job in jobs):
        return Decision("COMPLETE", None, "No outstanding missions")
    if any(job.state == "RUNNING" for job in jobs):
        return Decision("WAIT", None, "Active operation must reach a safe checkpoint")

    # The upstream validator, not the model, must authenticate these states.
    ready = sorted(
        (job for job in jobs if job.state == "READY" and
         all(by_id[d].state == "DONE" for d in job.dependencies)),
        key=lambda job: (-job.priority, job.id),
    )
    blocked = []
    deferred = []
    decisions = []
    grants = tuple(grants)
    for job in ready:
        if job.operation in FORBIDDEN_OPERATIONS or job.operation not in KNOWN_OPERATIONS:
            blocked.append(Decision("BLOCKED", job.id, "Forbidden or unknown operation"))
            continue
        if job.failures_without_progress >= retry_limit:
            blocked.append(Decision("BLOCKED", job.id, "Retry budget exhausted without progress"))
            continue
        if job.product_decision_required:
            decisions.append(Decision("REQUIRES_DECISION", job.id, "Unspecified product decision"))
            continue
        if not any(g.allows(job.repo, job.operation, now_epoch) for g in grants):
            decisions.append(Decision("REQUIRES_DECISION", job.id, "No active scoped grant"))
            continue
        if job.external_required and not online:
            deferred.append(Decision("WAIT_FOR_NETWORK", job.id, "External verification pending"))
            continue
        return Decision("RUN", job.id, "Ready and authorized")
    if blocked:
        return blocked[0]
    if deferred:
        return deferred[0]
    if decisions:
        return decisions[0]
    if any(job.state == "BLOCKED" for job in jobs):
        return Decision("BLOCKED", None, "No runnable independent missions")
    return Decision("WAIT", None, "Waiting on dependencies")

"""Fail-closed topology gate for non-destructive Git branch publication."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from .github_full_evidence import GitHubEvidenceError


MAX_TOPOLOGY_AGE_SECONDS = 60
_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class PushDisposition(str, Enum):
    NEW_BRANCH = "NEW_BRANCH"
    FAST_FORWARD = "FAST_FORWARD"
    ALREADY_UP_TO_DATE = "ALREADY_UP_TO_DATE"


@dataclass(frozen=True)
class GitPushState:
    repository: str
    branch: str
    candidate_sha: str
    local_head: str
    remote_main_head: str
    remote_branch_head: str | None
    candidate_contains_main: bool
    remote_branch_is_ancestor: bool | None
    observed_at: int


def validate_push_state(
    state: GitPushState,
    *,
    repository: str,
    branch: str,
    candidate_sha: str,
    now_epoch: int,
) -> PushDisposition:
    """Require a fresh fetch-derived topology that needs no history rewrite."""
    if (not isinstance(state, GitPushState)
            or not isinstance(repository, str) or not _REPO.fullmatch(repository)
            or not isinstance(branch, str) or not _BRANCH.fullmatch(branch)
            or branch in {"main", "master"} or branch.startswith("refs/")
            or ".." in branch or "//" in branch
            or not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha)
            or type(now_epoch) is not int or now_epoch < 0):
        raise GitHubEvidenceError("Invalid trusted Git topology expectation")
    remote_branch = state.remote_branch_head
    if (state.repository != repository or state.branch != branch
            or state.candidate_sha != candidate_sha
            or state.local_head != candidate_sha
            or not isinstance(state.remote_main_head, str)
            or not _SHA.fullmatch(state.remote_main_head)
            or remote_branch is not None
            and (not isinstance(remote_branch, str) or not _SHA.fullmatch(remote_branch))
            or type(state.candidate_contains_main) is not bool
            or (state.remote_branch_is_ancestor is not None
                and type(state.remote_branch_is_ancestor) is not bool)
            or type(state.observed_at) is not int
            or state.observed_at > now_epoch + 5
            or now_epoch - state.observed_at > MAX_TOPOLOGY_AGE_SECONDS):
        raise GitHubEvidenceError("Git topology is stale or belongs to another push")
    if not state.candidate_contains_main:
        raise GitHubEvidenceError("Candidate does not contain fetched origin/main")
    if remote_branch is None:
        if state.remote_branch_is_ancestor is not None:
            raise GitHubEvidenceError("New branch has contradictory remote topology")
        return PushDisposition.NEW_BRANCH
    if state.remote_branch_is_ancestor is not True:
        raise GitHubEvidenceError("Remote branch diverged; force-push is forbidden")
    if remote_branch == candidate_sha:
        return PushDisposition.ALREADY_UP_TO_DATE
    return PushDisposition.FAST_FORWARD

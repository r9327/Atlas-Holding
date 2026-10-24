"""Strict presentation boundary for selected-project Git status."""
from __future__ import annotations

import re

_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def validate_project_status(*, project: str, repository: str,
                            git_status: object, eligible_missions: int) -> str:
    """Display only independently supplied status; never infer permissions."""
    if not isinstance(project, str) or not 1 <= len(project) <= 80 or any(ord(c) < 32 for c in project):
        raise ValueError("Invalid project name")
    if not isinstance(repository, str) or not _REPO.fullmatch(repository):
        raise ValueError("Invalid repository")
    if type(eligible_missions) is not int or not 0 <= eligible_missions <= 2000:
        raise ValueError("Invalid authorized mission count")
    if git_status is None:
        git_label = "non vérifié"
    else:
        if getattr(git_status, "repository", None) != repository:
            raise ValueError("Git status belongs to another repository")
        sha = getattr(git_status, "head", None)
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise ValueError("Invalid Git HEAD")
        if getattr(git_status, "remote_checked", None) is not True:
            git_label = "local seulement"
        else:
            ahead = getattr(git_status, "ahead", None)
            behind = getattr(git_status, "behind", None)
            remote = getattr(git_status, "remote_head", None)
            if remote is not None and (not isinstance(remote, str) or not _SHA.fullmatch(remote)):
                raise ValueError("Invalid remote SHA")
            if remote is None:
                if ahead is not None or behind is not None:
                    raise ValueError("Missing remote has ancestry counts")
                git_label = "branche distante absente"
            elif (type(ahead) is not int or type(behind) is not int
                  or ahead < 0 or behind < 0):
                raise ValueError("Invalid Git ancestry")
            elif ahead == behind == 0:
                if sha != remote:
                    raise ValueError("Conflicting equal Git ancestry")
                git_label = "à jour"
            elif ahead and behind:
                git_label = f"divergence (+{ahead}/-{behind})"
            elif ahead:
                git_label = f"en avance (+{ahead})"
            else:
                git_label = f"en retard (-{behind})"
        if getattr(git_status, "dirty", None) is True:
            git_label += " · modifications locales"
    return f"Projet : {project} · {repository} · Git : {git_label} · Missions éligibles : {eligible_missions}"

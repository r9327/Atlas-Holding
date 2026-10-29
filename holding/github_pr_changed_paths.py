"""F10: bounded, non-certifying changed-path hints from a pinned open PR head."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_PRIVATE = ("secret", "token", "password", "credential", "private",
            "api_key", "apikey", ".env", ".pem", ".p12", "id_rsa")


class PrChangedPathsError(ValueError):
    pass


@dataclass(frozen=True)
class PrChangedPaths:
    repository: str
    pr_number: int
    head_sha: str
    status: str
    listed_files: int
    paths: tuple[str, ...]
    can_certify: bool = False
    can_skip_implementation: bool = False


def _path(name: Any) -> bool:
    if not isinstance(name, str) or not 1 <= len(name) <= 240:
        return False
    if not _PATH.fullmatch(name) or any(p in {".", "..", ""}
                                           for p in name.split("/")):
        return False
    return not any(secret in part.casefold() for part in name.split("/")
                   for secret in _PRIVATE)


def parse_pr_changed_paths(
    repository: str, pr_number: int, head_sha: str, rows: Any,
) -> PrChangedPaths:
    if (not isinstance(repository, str) or not _REPO.fullmatch(repository)
            or any(x in {".", ".."} for x in repository.split("/"))
            or type(pr_number) is not int or pr_number < 1
            or not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha)
            or not isinstance(rows, list) or len(rows) > 100):
        raise PrChangedPathsError("Invalid bounded PR files observation")
    names: list[str] = []
    seen: set[str] = set()
    omitted = 0
    for row in rows:
        if not isinstance(row, dict):
            raise PrChangedPathsError("Invalid PR file entry")
        filename = row.get("filename")
        status = row.get("status")
        if (not isinstance(filename, str) or not filename
                or len(filename) > 1024 or "\n" in filename or "\r" in filename
                or not isinstance(status, str) or status not in {
                    "added", "modified", "removed", "renamed", "changed", "copied"}):
            raise PrChangedPathsError("Untrusted PR changed path")
        if filename in seen:
            raise PrChangedPathsError("Duplicate PR changed path")
        seen.add(filename)
        if _path(filename) and len(names) < 20:
            names.append(filename)
        else:
            omitted += 1
    status = ("PR_PATHS_PARTIAL" if omitted or len(rows) == 100 or not rows
              else "PR_PATHS_BOUNDED")
    return PrChangedPaths(repository, pr_number, head_sha,
                          status, len(rows), tuple(names))


def validate_pr_changed_paths(repository: str, result: Any) -> PrChangedPaths:
    if (not isinstance(result, dict)
            or set(result) != {
                "repository", "pr_number", "head_sha", "status",
                "listed_files", "paths", "can_certify",
                "can_skip_implementation"}):
        raise PrChangedPathsError("Invalid broker PR paths response")
    if (result.get("repository") != repository
            or not isinstance(repository, str) or not _REPO.fullmatch(repository)
            or type(result["pr_number"]) is not int or result["pr_number"] < 1
            or not isinstance(result["head_sha"], str)
            or not _SHA.fullmatch(result["head_sha"])
            or not isinstance(result["status"], str)
            or result["status"] not in {"PR_PATHS_PARTIAL", "PR_PATHS_BOUNDED"}
            or type(result["listed_files"]) is not int
            or not 0 <= result["listed_files"] <= 100
            or not isinstance(result["paths"], list)
            or len(result["paths"]) > 20
            or len(result["paths"]) > result["listed_files"]
            or len(set(map(repr, result["paths"]))) != len(result["paths"])
            or any(not _path(p) for p in result["paths"])
            or result["can_certify"] is not False
            or result["can_skip_implementation"] is not False
            or (result["status"] == "PR_PATHS_BOUNDED"
                and (result["listed_files"] != len(result["paths"])
                     or result["listed_files"] in (0, 100)))):
        raise PrChangedPathsError("Invalid or contradictory broker PR paths")
    return PrChangedPaths(
        repository, result["pr_number"], result["head_sha"],
        result["status"], result["listed_files"], tuple(result["paths"]))

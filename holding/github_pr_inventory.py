"""F10: limited read-only observations of currently open GitHub pull requests.

Observed metadata is not an authorization to change Git, nor a guarantee that
a title or branch represents completed functionality. Broker evidence is
transient; never equate the list with a certified set of implementations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class PrInventoryError(ValueError):
    pass


@dataclass(frozen=True)
class OpenPrHint:
    number: int
    head_sha: str
    base_sha: str
    head_branch: str
    draft: bool
    same_repository: bool


@dataclass(frozen=True)
class OpenPrInventory:
    repository: str
    status: str
    observed_count: int
    hints: tuple[OpenPrHint, ...]
    can_certify: bool = False
    can_skip_implementation: bool = False


def _identity(repository: str) -> bool:
    return (isinstance(repository, str)
            and bool(_REPO.fullmatch(repository))
            and all(p not in {".", ".."} for p in repository.split("/")))


def parse_open_prs(repository: str, rows: Any) -> OpenPrInventory:
    """Parse the first 100 API entries, never silently assert completeness.

    The provider API is requested with `per_page=100`. Exactly 100 results
    means pagination is unresolved, even if only a few are relevant.
    """
    if not _identity(repository) or not isinstance(rows, list) or len(rows) > 100:
        raise PrInventoryError("Invalid bounded GitHub PR observation")
    hints: list[OpenPrHint] = []
    seen: set[int] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise PrInventoryError("Malformed GitHub PR entry")
        number, draft, state = raw.get("number"), raw.get("draft"), raw.get("state")
        head, base = raw.get("head"), raw.get("base")
        if (type(number) is not int or number < 1 or number in seen
                or type(draft) is not bool or state != "open"
                or not isinstance(head, dict) or not isinstance(base, dict)):
            raise PrInventoryError("Invalid GitHub PR identity")
        seen.add(number)
        head_repo = head.get("repo")
        base_repo = base.get("repo")
        if (not isinstance(base_repo, dict)
                or base_repo.get("full_name") != repository
                or not isinstance(head_repo, dict)
                or not _identity(head_repo.get("full_name"))
                or not isinstance(head.get("sha"), str)
                or not _SHA.fullmatch(head["sha"])
                or not isinstance(base.get("sha"), str)
                or not _SHA.fullmatch(base["sha"])
                or not isinstance(head.get("ref"), str)
                or not _BRANCH.fullmatch(head["ref"])
                or ".." in head["ref"].split("/")
                or any(segment in {".", ""} for segment in head["ref"].split("/"))):
            raise PrInventoryError("Invalid or cross-repository PR metadata")
        if len(hints) < 20:
            hints.append(OpenPrHint(
                number, head["sha"], base["sha"], head["ref"], draft,
                head_repo["full_name"] == repository))
    return OpenPrInventory(
        repository,
        "PAGINATION_NOT_VERIFIED" if len(rows) == 100 else "OPEN_PRS_OBSERVED",
        len(rows), tuple(hints))


def validate_open_pr_inventory(repository: str, result: Any) -> OpenPrInventory:
    """Revalidate the privileged broker response in the unprivileged caller."""
    if not _identity(repository) or not isinstance(result, dict) or set(result) != {
            "repository", "status", "observed_count", "hints",
            "can_certify", "can_skip_implementation"}:
        raise PrInventoryError("Malformed privileged PR inventory response")
    if (result["repository"] != repository
            or not isinstance(result["status"], str)
            or result["status"] not in {"PAGINATION_NOT_VERIFIED", "OPEN_PRS_OBSERVED"}
            or type(result["observed_count"]) is not int
            or not 0 <= result["observed_count"] <= 100
            or (result["status"] == "PAGINATION_NOT_VERIFIED")
               != (result["observed_count"] == 100)
            or result["can_certify"] is not False
            or result["can_skip_implementation"] is not False
            or not isinstance(result["hints"], list)
            or len(result["hints"]) != min(result["observed_count"], 20)):
        raise PrInventoryError("Contradictory privileged PR evidence")
    seen: set[int] = set()
    hints = []
    for item in result["hints"]:
        if not isinstance(item, dict) or set(item) != set(asdict(
                OpenPrHint(1, "a"*40, "a"*40, "x", False, False))):
            raise PrInventoryError("Malformed privileged PR hint")
        number, head_sha, base_sha, head_branch, draft, same_repo = (
            item["number"], item["head_sha"], item["base_sha"],
            item["head_branch"], item["draft"], item["same_repository"])
        if (type(number) is not int or number < 1 or number in seen
                or not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha)
                or not isinstance(base_sha, str) or not _SHA.fullmatch(base_sha)
                or not isinstance(head_branch, str)
                or not _BRANCH.fullmatch(head_branch)
                or any(x in {"", ".", ".."} for x in head_branch.split("/"))
                or type(draft) is not bool or type(same_repo) is not bool):
            raise PrInventoryError("Invalid privileged PR hint")
        seen.add(number)
        hints.append(OpenPrHint(number, head_sha, base_sha, head_branch, draft, same_repo))
    return OpenPrInventory(repository, result["status"], result["observed_count"],
                           tuple(hints))

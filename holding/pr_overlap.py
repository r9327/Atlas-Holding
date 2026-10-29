"""F10: advisory exact-path overlap for one relevant open PR only.

Relevance is a branch-name heuristic, never a claim of duplicate work.
The comparison contains no patches, secrets, permissions or certification.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .github_pr_inventory import OpenPrHint, OpenPrInventory
from .github_pr_changed_paths import PrChangedPaths


@dataclass(frozen=True)
class PrPathOverlap:
    pr_number: int
    head_sha: str
    status: str
    observed_paths: tuple[str, ...]
    matching_local_paths: tuple[str, ...]
    can_certify: bool = False
    can_skip_implementation: bool = False


_STOP = frozenset({
    "feat", "feature", "fix", "bug", "issue", "chore", "code",
    "test", "tests", "with", "from", "must", "should", "change",
    "new", "existing", "implementation", "module", "source",
})


def _words(value: str) -> frozenset[str]:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return frozenset(
        x for x in re.findall(r"[a-z][a-z0-9]{3,}", expanded.lower())
        if x not in _STOP
    )


def choose_relevant_pr(
    report: OpenPrInventory, *, acceptance: tuple[str, ...],
    candidate_paths: tuple[str, ...],
) -> OpenPrHint | None:
    """Select at most one PR with an actual lexical branch overlap.

    The absence of a result is never evidence that no PR overlaps the task.
    """
    terms: set[str] = set()
    for criterion in acceptance[:30]:
        terms.update(_words(criterion[:1200]))
    for path in candidate_paths[:12]:
        terms.update(_words(path.rsplit("/", 1)[-1].replace("_", " ")))
    if not terms:
        return None
    eligible: list[tuple[int, int, OpenPrHint]] = []
    for hint in report.hints[:20]:
        branch_terms = _words(hint.head_branch.replace("-", " ").replace("_", " "))
        score = len(terms.intersection(branch_terms))
        if score:
            eligible.append((-score, hint.number, hint))
    return min(eligible)[2] if eligible else None


def compare_pr_paths(
    hint: OpenPrHint, report: PrChangedPaths,
    candidate_paths: tuple[str, ...],
) -> PrPathOverlap:
    if (report.pr_number != hint.number or report.head_sha != hint.head_sha
            or report.can_certify or report.can_skip_implementation):
        raise ValueError("PR path evidence mismatched the selected head")
    paths = tuple(report.paths[:20])
    local = set(candidate_paths[:12])
    matches = tuple(path for path in paths if path in local)
    return PrPathOverlap(
        hint.number, hint.head_sha,
        ("EXACT_PATH_OVERLAP" if matches else
         "NO_MATCH_IN_PARTIAL_PR_SCAN" if report.status == "PR_PATHS_PARTIAL"
         else "NO_EXACT_PATH_MATCH"),
        paths, matches,
    )

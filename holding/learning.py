"""Measured-only strategy promotion; never accept self-reported model scores.

A trusted external test runner must authenticate these evaluations.
This module cannot change permissions, CI, Doctor or repositories.
"""
from __future__ import annotations
from dataclasses import dataclass
import re


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Evaluation:
    strategy_id: str
    suite_id: str
    suite_sha256: str
    passed: int
    total: int
    critical_failures: int
    security_failures: int
    regressions: int
    source: str  # must be "trusted_test_runner"

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass(frozen=True)
class Promotion:
    action: str
    reason: str


def evaluate_candidate(current: Evaluation, candidate: Evaluation, *, min_trials: int = 10) -> Promotion:
    """Fail-closed promotion of tested prompt/model/workflow strategies only.

    It is *not* automatic self-modification of executable code or safety policy.
    """
    def valid(e: Evaluation) -> bool:
        if type(e) is not Evaluation:
            return False
        counts = (e.passed, e.total, e.critical_failures,
                  e.security_failures, e.regressions)
        return (
            type(e.strategy_id) is str and bool(e.strategy_id)
            and type(e.suite_id) is str and bool(e.suite_id)
            and type(e.suite_sha256) is str
            and _SHA256.fullmatch(e.suite_sha256) is not None
            and e.source == "trusted_test_runner"
            and all(type(value) is int for value in counts)
            and type(min_trials) is int and min_trials > 0
            and e.total >= min_trials
            and 0 <= e.passed <= e.total
            and all(0 <= value <= e.total for value in counts[2:])
        )

    if not valid(current) or not valid(candidate):
        return Promotion("REJECT", "Insufficient or untrusted benchmark evidence")
    if (current.suite_id, current.suite_sha256) != (candidate.suite_id, candidate.suite_sha256):
        return Promotion("REJECT", "Candidate evaluated on a different suite")
    if current.total != candidate.total:
        return Promotion("REJECT", "Candidate did not execute the complete comparison corpus")
    if current.strategy_id == candidate.strategy_id:
        return Promotion("KEEP", "No new strategy to compare")
    if candidate.critical_failures or candidate.security_failures or candidate.regressions:
        return Promotion("REJECT", "Candidate caused critical, security or regression failures")
    if candidate.rate <= current.rate:
        return Promotion("KEEP", "No measured improvement")
    return Promotion("PROMOTE", "Candidate improves independent test outcomes")

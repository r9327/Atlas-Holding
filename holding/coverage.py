"""Independent coverage inventory: roadmap acceptance -> task -> exact SHA -> proof.

Never infers a passed requirement from an LLM response, a checkbox, or local
Doctor advice. The Control Center can page through real SQLite evidence without
materializing the entire DAG, and export a fail-closed closure assessment.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterator

from .state_store import StateStore, StoreError


@dataclass(frozen=True)
class Requirement:
    mission_id: str
    criterion_number: int
    criterion: str
    state: str
    candidate_sha: str
    proof_sha: str
    status: str


@dataclass(frozen=True)
class CoverageSummary:
    requirements_total: int
    proven: int
    awaiting_proof: int
    blocked: int
    incomplete: int
    missions_total: int
    ready_to_close: bool
    unmapped_missions: int = 0


class RequirementCoverage:
    """Read-only audit. A database is not evidence without the signed-proof gate."""

    def __init__(self, store: StateStore):
        self.store = store

    def _has_table(self, name: str) -> bool:
        return bool(self.store.db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,)).fetchone()[0])

    def _rows(self, *, after_id: str = "", include_after: bool = False,
              batch: int = 128) -> Iterator:
        if not self._has_table("mission_specs"):
            return
        signed = self._has_table("signed_proofs")
        fields = ("p.candidate_sha AS proof_sha,p.verdict AS proof_verdict"
                  if signed else "'' AS proof_sha,'' AS proof_verdict")
        join = ("LEFT JOIN signed_proofs p ON p.mission_id=m.id"
                if signed else "")
        while True:
            # Keyset pagination: do not rescan all prior missions when
            # resuming in the middle of a long roadmap.
            operator = ">=" if include_after else ">"
            rows = self.store.db.execute(
                f"SELECT m.id,m.state,m.candidate_sha,m.evidence_sha,"
                f"s.spec_json,{fields} FROM mission_specs s "
                f"JOIN missions m ON m.id=s.id {join} "
                f"WHERE m.id{operator}? ORDER BY m.id LIMIT ?",
                (after_id, batch)).fetchall()
            if not rows:
                return
            for row in rows:
                yield row
            after_id = rows[-1]["id"]
            include_after = False

    @staticmethod
    def _criteria(row) -> list[str]:
        try:
            spec = json.loads(row["spec_json"])
        except (ValueError, TypeError) as exc:
            raise StoreError("Damaged roadmap requirement specification") from exc
        criteria = spec.get("acceptance") if isinstance(spec, dict) else None
        if (not isinstance(criteria, list) or not criteria
                or any(not isinstance(x, str) or not x.strip() for x in criteria)
                or spec.get("id") != row["id"]):
            raise StoreError("Missing or corrupted requirement-to-mission mapping")
        return criteria

    @staticmethod
    def _status(row) -> str:
        if row["state"] == "DONE":
            if (row["candidate_sha"] and row["candidate_sha"] == row["evidence_sha"]
                    and row["candidate_sha"] == row["proof_sha"]
                    and row["proof_verdict"] == "PASS"):
                return "PROVEN"
            return "UNVERIFIED"
        if row["state"] == "BLOCKED":
            return "BLOCKED"
        if row["state"] == "QA_PENDING":
            return "AWAITING_PROOF"
        return "INCOMPLETE"

    def summary(self) -> CoverageSummary:
        total = proven = awaiting = blocked = incomplete = missions = 0
        for row in self._rows():
            n = len(self._criteria(row))
            status = self._status(row)
            missions += 1
            total += n
            if status == "PROVEN":
                proven += n
            elif status == "AWAITING_PROOF":
                awaiting += n
            elif status == "BLOCKED":
                blocked += n
            else:
                incomplete += n
        # Every mission, even one inserted without a roadmap specification,
        # must block closure until explicit acceptance coverage is imported.
        all_missions = self.store.db.execute(
            "SELECT COUNT(*) FROM missions").fetchone()[0]
        untracked = max(0, int(all_missions) - missions)
        return CoverageSummary(total, proven, awaiting, blocked, incomplete,
                               int(all_missions),
                               total > 0 and total == proven and untracked == 0,
                               untracked)

    def page(self, *, after_mission_id: str = "", after_criterion: int = -1,
             limit: int = 100) -> tuple[Requirement, ...]:
        if (not isinstance(after_mission_id, str)
                or type(after_criterion) is not int or after_criterion < -1
                or type(limit) is not int or not 1 <= limit <= 500):
            raise ValueError("Invalid bounded coverage cursor or limit")
        result: list[Requirement] = []
        for row in self._rows(after_id=after_mission_id,
                              include_after=bool(after_mission_id)):
            mid = row["id"]
            if mid < after_mission_id:
                continue
            for index, criterion in enumerate(self._criteria(row)):
                if mid == after_mission_id and index <= after_criterion:
                    continue
                result.append(Requirement(
                    mid, index, criterion, row["state"], row["candidate_sha"],
                    row["evidence_sha"] if self._status(row) == "PROVEN" else "",
                    self._status(row)))
                if len(result) == limit:
                    return tuple(result)
        return tuple(result)

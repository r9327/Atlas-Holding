"""Fail-closed, durable roadmap closure evidence for the Control Center.

A report contains immutable mission->SHA->signed receipt mappings. Its creation
never changes mission state, merges a PR, or counts an advisory Doctor run as
a certificate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

from .coverage import RequirementCoverage, CoverageSummary
from .state_store import StateStore, StoreError

_MAX_REPORT = 2 * 1024 * 1024


@dataclass(frozen=True)
class ClosureStatus:
    ready: bool
    reason: str
    requirements: CoverageSummary
    report_digest: str
    mission_proofs: tuple[dict, ...]


class RoadmapClosure:
    def __init__(self, store: StateStore):
        self.store = store

    def inspect(self) -> ClosureStatus:
        summary = RequirementCoverage(self.store).summary()
        if not summary.ready_to_close:
            return ClosureStatus(False, "ROADMAP_REQUIREMENTS_UNPROVEN", summary, "", ())
        rows = self.store.db.execute("""
            SELECT m.id,m.repo,m.candidate_sha,m.evidence_sha,m.state,
                   p.receipt_digest,p.verdict,p.candidate_sha AS signed_sha
              FROM missions m
              LEFT JOIN signed_proofs p ON p.mission_id=m.id
             ORDER BY m.id
        """).fetchall()
        if len(rows) != summary.missions_total or not rows:
            return ClosureStatus(False, "MISSING_MISSION_PROVENANCE", summary, "", ())
        checked = []
        for row in rows:
            if (row["state"] != "DONE" or len(row["candidate_sha"]) != 40
                    or row["candidate_sha"] != row["evidence_sha"]
                    or row["candidate_sha"] != row["signed_sha"]
                    or row["verdict"] != "PASS"
                    or not isinstance(row["receipt_digest"], str)
                    or len(row["receipt_digest"]) != 64):
                return ClosureStatus(False, "MISSING_FINAL_SIGNED_PROOF", summary, "", ())
            checked.append({"mission_id": row["id"], "repo": row["repo"],
                            "candidate_sha": row["candidate_sha"],
                            "receipt_digest": row["receipt_digest"]})
        versions = []
        present = self.store.db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='roadmap_versions'").fetchone()[0]
        if not present:
            return ClosureStatus(False, "MISSING_ROADMAP_SOURCE", summary, "", ())
        for row in self.store.db.execute(
                "SELECT repo,version,digest FROM roadmap_versions ORDER BY version"):
            versions.append(dict(row))
        if not versions:
            return ClosureStatus(False, "MISSING_ROADMAP_SOURCE", summary, "", ())
        payload = {"schema": 1, "roadmap_versions": versions,
                   "coverage": asdict(summary), "mission_proofs": checked}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        if len(encoded) > _MAX_REPORT:
            raise StoreError("Closure report exceeds size budget")
        return ClosureStatus(True, "ALL_REQUIREMENTS_SIGNED", summary,
                             hashlib.sha256(encoded).hexdigest(), tuple(checked))

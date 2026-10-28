"""Safe, atomic roadmap amendment at mission checkpoints (never history rewrite).

A new directive may append missions and revise *untouched READY* missions.
Missing tasks and changes to committed, running, signed or quarantined work
are explicit conflicts. This is a trusted Control Center action, not an
LLM tool and never authorizes new operations by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import time

from .autonomy import Mission, choose_next
from .roadmap_import import (
    MAX_ROADMAP_BYTES, _document, _parse_missions,
)
from .schema import ROADMAP_AMENDMENT_SCHEMA, apply_schema
from .state_store import StateStore, StoreError


@dataclass(frozen=True)
class ReplanResult:
    version: int
    digest: str
    added: tuple[str, ...]
    revised: tuple[str, ...]
    preserved: tuple[str, ...]
    blocked: tuple[str, ...]
    applied: bool


@dataclass(frozen=True)
class MissionSpecRevision:
    mission_id: str
    repository: str
    version: int
    replaced_by_version: int
    spec_sha256: str
    specification: dict
    archived_epoch: int


class SafeRoadmapReplanner:
    def __init__(self, store: StateStore):
        # Preview is genuinely read-only: no auto-migration, DDL, or events.
        # The initial roadmap must first enter through RoadmapImporter.
        self.store = store

    def _require_existing_roadmap(self):
        names = {row[0] for row in self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('roadmap_versions','mission_specs')")}
        if names != {'roadmap_versions', 'mission_specs'}:
            raise StoreError("Import an initial roadmap before using replan")

    @staticmethod
    def _parse(raw: str, *, incremental: bool = False):
        if not isinstance(raw, str):
            raise StoreError("Roadmap revision must be text")
        try:
            if len(raw.encode("utf-8")) > MAX_ROADMAP_BYTES:
                raise StoreError("Roadmap revision exceeds 2 MiB")
        except UnicodeError as exc:
            raise StoreError("Invalid roadmap text encoding") from exc
        doc = _document(raw)
        jobs, specs = _parse_missions(
            doc, allow_external_dependencies=incremental)
        digest = hashlib.sha256(json.dumps(
            doc, sort_keys=True, ensure_ascii=False,
            separators=(",", ":")).encode()).hexdigest()
        return doc, jobs, specs, digest

    def _classify(self, doc, jobs, specs, digest):
        existing_digest = self.store.db.execute(
            "SELECT version FROM roadmap_versions WHERE digest=?",
            (digest,)).fetchone()
        old = {m.id: m for m in self.store.missions()}
        tracked = {
            row["id"]: row["spec_json"]
            for row in self.store.db.execute(
                "SELECT s.id,s.spec_json FROM mission_specs s "
                "JOIN missions m ON m.id=s.id WHERE m.repo=?", (doc["repo"],))
        }
        desired = {j.id: j for j in jobs}
        if existing_digest:
            if (set(tracked) == set(desired)
                    and all(tracked.get(j.id) == specs[j.id] for j in jobs)):
                return ReplanResult(existing_digest["version"], digest, (), (),
                                    tuple(j.id for j in jobs), (), False)
            return ReplanResult(0, digest, (), (), (), ("STALE_VERSION_REPLAY",), False)
        missing = sorted(set(tracked) - set(desired))
        blocked = [f"OMITTED:{mid}" for mid in missing]
        # Legacy tasks without acceptance contracts cannot disappear from a
        # revision just because they weren't present in the last import.
        for mid, prior in old.items():
            if prior.repo == doc["repo"] and mid not in tracked:
                blocked.append(f"LEGACY_UNTRACKED:{mid}")
        added, revised, preserved = [], [], []
        for job in jobs:
            prior = old.get(job.id)
            if prior is None:
                added.append(job.id)
                continue
            if prior.repo != doc["repo"] or job.id not in tracked:
                blocked.append(f"FOREIGN_OR_UNTRACKED:{job.id}")
                continue
            if tracked[job.id] == specs[job.id]:
                preserved.append(job.id)
                continue
            # Operation-changing amendments require explicit architectural
            # migration, never a silent change of executor or authority.
            if prior.operation != job.operation:
                blocked.append(f"OPERATION_CHANGE:{job.id}")
            elif (prior.state != "READY" or prior.candidate_sha
                  or prior.evidence_sha or prior.failures_without_progress):
                blocked.append(f"ALREADY_STARTED:{job.id}")
            elif self.store.db.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' "
                    "AND name='git_operations'").fetchone()[0] and self.store.db.execute(
                        "SELECT count(*) FROM git_operations WHERE mission_id=?",
                        (job.id,)).fetchone()[0]:
                blocked.append(f"GIT_HISTORY:{job.id}")
            elif any(job.id in m.dependencies and m.state not in ("READY", "CANCELLED")
                     for m in old.values()):
                blocked.append(f"ACTIVE_DEPENDENT:{job.id}")
            else:
                revised.append(job.id)
        # Compare the proposed DAG with all unchanged other-repo missions.
        proposed = {
            mid: task for mid, task in old.items() if task.repo != doc["repo"]
        }
        for job in jobs:
            original = old.get(job.id)
            proposed[job.id] = (replace(original,
                dependencies=job.dependencies, priority=job.priority,
                external_required=job.external_required,
                product_decision_required=job.product_decision_required)
                if original is not None else job)
        decision = choose_next(tuple(proposed.values()), (), now_epoch=int(time.time()),
                               online=False)
        if decision.action == "BLOCKED" and decision.reason in {
                "Dependency cycle", "Unknown dependency",
                "Duplicate or blank task IDs", "Missing or stale completion evidence"}:
            blocked.append("INVALID_DAG:" + decision.reason)
        return ReplanResult(0, digest, tuple(added), tuple(revised),
                            tuple(preserved), tuple(sorted(set(blocked))), False)

    def preview(self, raw: str) -> ReplanResult:
        self._require_existing_roadmap()
        doc, jobs, specs, digest = self._parse(raw)
        return self._classify(doc, jobs, specs, digest)

    def history(self, mission_id: str, *, limit: int = 20,
                before_version: int | None = None) -> tuple[MissionSpecRevision, ...]:
        """Read verified prior contracts, newest first; never install a schema."""
        if (not isinstance(mission_id, str) or not mission_id.strip()
                or len(mission_id) > 80 or type(limit) is not int or not 1 <= limit <= 100
                or (before_version is not None
                    and (type(before_version) is not int or before_version < 1))):
            raise StoreError("Invalid mission history request")
        mission = self.store.db.execute(
            "SELECT repo FROM missions WHERE id=?", (mission_id,)).fetchone()
        if mission is None:
            raise StoreError("Unknown mission for history")
        if not self.store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='mission_spec_history'").fetchone():
            return ()
        query = "SELECT * FROM mission_spec_history WHERE mission_id=?"
        args: list = [mission_id]
        if before_version is not None:
            query += " AND version<?"
            args.append(before_version)
        query += " ORDER BY version DESC LIMIT ?"
        args.append(limit)
        revisions = []
        for row in self.store.db.execute(query, args):
            try:
                digest = hashlib.sha256(row["spec_json"].encode("utf-8")).hexdigest()
                spec = json.loads(row["spec_json"])
                if (digest != row["spec_sha256"] or not isinstance(spec, dict)
                        or spec.get("id") != mission_id
                        or row["version"] >= row["replaced_by_version"]):
                    raise ValueError("Archived contract provenance mismatch")
            except (ValueError, TypeError, UnicodeError) as exc:
                raise StoreError("Corrupt archived mission specification") from exc
            revisions.append(MissionSpecRevision(
                mission_id, mission["repo"], row["version"], row["replaced_by_version"],
                digest, spec, row["archived_epoch"]))
        return tuple(revisions)

    def _expand_direction(self, raw: str) -> tuple[str, str]:
        """Merge explicit incremental requirements with all stored prior work."""
        self._require_existing_roadmap()
        doc, _jobs, specifications, delta_digest = self._parse(raw, incremental=True)
        merged: dict[str, dict] = {}
        stored_goals: set[str] = set()
        for row in self.store.db.execute(
                "SELECT s.id,s.spec_json FROM mission_specs s "
                "JOIN missions m ON m.id=s.id WHERE m.repo=? ORDER BY m.id",
                (doc["repo"],)):
            try:
                item = json.loads(row["spec_json"])
            except (ValueError, TypeError) as exc:
                raise StoreError("Corrupt existing roadmap during delta import") from exc
            if not isinstance(item, dict) or item.get("id") != row["id"]:
                raise StoreError("Existing roadmap identity mismatch")
            # Specs hold an immutable copy of the direction, but task fields
            # cannot impersonate a top-level project goal on re-import.
            old_goal = item.pop("project_goal", None)
            if old_goal is not None:
                stored_goals.add(old_goal)
            merged[row["id"]] = item
        if len(stored_goals) > 1:
            raise StoreError("Conflicting stored project goals require full replan")
        old_goal = next(iter(stored_goals), None)
        new_goal = doc.get("project_goal")
        if old_goal is not None and new_goal is not None and old_goal != new_goal:
            raise StoreError("Project goal change requires full roadmap replan")
        for mid, encoded in specifications.items():
            mission = json.loads(encoded)
            mission.pop("project_goal", None)
            merged[mid] = mission
        expanded_doc = {"repo": doc["repo"], "missions": list(merged.values())}
        effective_goal = new_goal if new_goal is not None else old_goal
        if effective_goal is not None:
            expanded_doc["project_goal"] = effective_goal
        expanded = json.dumps(expanded_doc, ensure_ascii=False,
                              separators=(",", ":"))
        if len(expanded.encode("utf-8")) > MAX_ROADMAP_BYTES:
            raise StoreError("Expanded roadmap exceeds 2 MiB")
        return expanded, delta_digest

    def preview_direction(self, raw: str) -> ReplanResult:
        """Read-only classification of a new user direction."""
        expanded, _ = self._expand_direction(raw)
        return self.preview(expanded)

    def append_direction(self, raw: str, *, source: str) -> ReplanResult:
        """Add goals without re-sending or dropping any previous roadmap lot."""
        expanded, delta_digest = self._expand_direction(raw)
        provenance = f"{source} delta_sha256:{delta_digest}"
        if len(provenance) > 500:
            raise StoreError("Direction source provenance exceeds 500 characters")
        return self.apply(expanded, source=provenance)

    def apply(self, raw: str, *, source: str) -> ReplanResult:
        self._require_existing_roadmap()
        if not isinstance(source, str) or not source.strip() or len(source) > 500:
            raise StoreError("Auditable roadmap revision provenance is required")
        doc, jobs, specs, digest = self._parse(raw)
        with self.store._transaction():
            plan = self._classify(doc, jobs, specs, digest)
            if plan.blocked:
                raise StoreError("Roadmap amendment conflicts: " +
                                 ",".join(plan.blocked[:20]))
            if plan.version:
                return plan
            apply_schema(self.store.db, ROADMAP_AMENDMENT_SCHEMA)
            now = int(time.time())
            self.store.db.execute(
                "INSERT INTO roadmap_versions(repo,digest,source,imported_epoch) "
                "VALUES(?,?,?,?)", (doc["repo"], digest, source, now))
            version = self.store.db.execute(
                "SELECT last_insert_rowid()").fetchone()[0]
            by_id = {j.id: j for j in jobs}
            for mid in plan.added:
                job = by_id[mid]
                self.store.db.execute("""INSERT INTO missions
                    (id,repo,operation,dependencies,priority,state,candidate_sha,
                    evidence_sha,external_required,product_decision_required,
                    failures_without_progress,updated_at)
                    VALUES(?,?,?,?,?,'READY','','',?,?,0,?)""",
                    (job.id, job.repo, job.operation, json.dumps(job.dependencies),
                     job.priority, int(job.external_required),
                     int(job.product_decision_required), now))
                self.store.db.execute(
                    "INSERT INTO mission_specs(id,spec_json,version) VALUES(?,?,?)",
                    (mid, specs[mid], version))
                self.store._event(mid, "MISSION_IMPORTED", f"replan:{version}")
            for mid in plan.revised:
                job = by_id[mid]
                prior_spec = self.store.db.execute(
                    "SELECT spec_json,version FROM mission_specs WHERE id=?", (mid,)
                ).fetchone()
                if prior_spec is None:
                    raise StoreError("Missing mission contract during replan")
                self.store.db.execute(
                    "INSERT INTO mission_spec_history "
                    "(mission_id,version,replaced_by_version,spec_sha256,spec_json,archived_epoch) "
                    "VALUES(?,?,?,?,?,?)",
                    (mid, prior_spec["version"], version,
                     hashlib.sha256(prior_spec["spec_json"].encode("utf-8")).hexdigest(),
                     prior_spec["spec_json"], now))
                updated = self.store.db.execute("""UPDATE missions SET
                    dependencies=?, priority=?, external_required=?,
                    product_decision_required=?,updated_at=?
                    WHERE id=? AND state='READY' AND candidate_sha=''
                      AND evidence_sha='' AND failures_without_progress=0""",
                    (json.dumps(job.dependencies), job.priority,
                     int(job.external_required),
                     int(job.product_decision_required), now, mid))
                if updated.rowcount != 1:
                    raise StoreError("Concurrent mission modification during replan")
                self.store.db.execute(
                    "UPDATE mission_specs SET spec_json=?,version=? WHERE id=?",
                    (specs[mid], version, mid))
                self.store._event(mid, "MISSION_REVISED", f"roadmap:{version}")
            self.store.db.execute(
                "INSERT INTO roadmap_amendments(version,changed_ids_json,recorded_epoch) "
                "VALUES(?,?,?)", (version, json.dumps({
                    "added": plan.added, "revised": plan.revised,
                    "preserved": plan.preserved}), now))
            self.store._event(None, "ROADMAP_REPLANNED",
                              f"v{version}:{digest} +{len(plan.added)} ~{len(plan.revised)}")
            return ReplanResult(version, digest, plan.added, plan.revised,
                                plan.preserved, (), True)

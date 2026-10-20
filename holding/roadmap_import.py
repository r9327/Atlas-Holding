"""Transactional, deterministic roadmap ingest for Atlas Holding.

Only explicit machine-readable DAGs are accepted. Ambiguous prose must be
resolved by a planner upstream, never silently interpreted as executable code.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Any

from .autonomy import Mission, KNOWN_OPERATIONS, choose_next
from .state_store import StateStore, StoreError

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_ROADMAP_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ImportResult:
    digest: str
    version: int
    added: tuple[str, ...]
    preserved: tuple[str, ...]


def _document(raw: str) -> dict[str, Any]:
    source = raw.strip()
    if source.startswith('```'):
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", source, re.DOTALL | re.IGNORECASE)
        if match is None:
            raise StoreError('Expected a single fenced JSON roadmap')
        source = match.group(1)
    try:
        doc = json.loads(source)
    except (ValueError, TypeError) as exc:
        raise StoreError('Roadmap must contain valid JSON (or a fenced JSON block)') from exc
    if not isinstance(doc, dict) or set(doc) != {'repo', 'missions'}:
        raise StoreError('Roadmap requires exactly repo and missions')
    if (not isinstance(doc['repo'], str) or not _REPO.fullmatch(doc['repo']) or
        any(part in ('.', '..') for part in doc['repo'].split('/'))):
        raise StoreError('Invalid exact repository name')
    if not isinstance(doc['missions'], list) or not doc['missions']:
        raise StoreError('Roadmap has no missions')
    return doc


def _parse_missions(doc: dict[str, Any]) -> tuple[tuple[Mission, ...], dict[str, str]]:
    jobs = []
    specifications: dict[str, str] = {}
    for item in doc['missions']:
        if not isinstance(item, dict) or not {'id', 'operation', 'acceptance'} <= item.keys() or set(item) - {
            'id', 'operation', 'acceptance', 'depends_on', 'priority', 'external_required',
            'product_decision_required',
        }:
            raise StoreError('Every mission needs id, operation, acceptance and recognized fields')
        mid, op, acceptance = item['id'], item['operation'], item['acceptance']
        deps = item.get('depends_on', [])
        priority = item.get('priority', 0)
        if not isinstance(mid, str) or not _ID.fullmatch(mid) or mid in specifications:
            raise StoreError('Duplicate or unsafe mission id')
        if not isinstance(op, str) or op not in KNOWN_OPERATIONS:
            raise StoreError('Unknown or forbidden operation')
        if (not isinstance(acceptance, list) or not acceptance or
            any(not isinstance(a, str) or not a.strip() for a in acceptance)):
            raise StoreError('Nonempty acceptance criteria are required')
        if not isinstance(deps, list) or any(not isinstance(d, str) or not _ID.fullmatch(d) for d in deps) or len(set(deps)) != len(deps):
            raise StoreError('Invalid dependencies')
        if type(priority) is not int or not -1000 <= priority <= 1000:
            raise StoreError('Invalid mission priority')
        flags = [item.get('external_required', False), item.get('product_decision_required', False)]
        if any(type(f) is not bool for f in flags):
            raise StoreError('Invalid flags')
        normalized = dict(item, depends_on=deps, priority=priority,
                          external_required=flags[0], product_decision_required=flags[1])
        specifications[mid] = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        jobs.append(Mission(mid, doc['repo'], operation=op, dependencies=tuple(deps),
                            priority=priority, external_required=flags[0],
                            product_decision_required=flags[1]))
    checked = choose_next(jobs, (), now_epoch=int(time.time()), online=False)
    if checked.action == 'BLOCKED' and checked.reason in {
        'Dependency cycle', 'Unknown dependency', 'Duplicate or blank task IDs',
    }:
        raise StoreError(checked.reason)
    return tuple(jobs), specifications


class RoadmapImporter:
    """Stores roadmap metadata and missions in ONE SQLite transaction.

    Revisions may add tasks or change global priority indirectly by new tasks.
    Altering or deleting an existing mission requires a separate, reviewed
    replan procedure; no silent loss of checkpoints or completed work.
    """

    def __init__(self, store: StateStore):
        self.store = store
        self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS roadmap_versions (
                version INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                digest TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                imported_epoch INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mission_specs (
                id TEXT PRIMARY KEY REFERENCES missions(id),
                spec_json TEXT NOT NULL,
                version INTEGER NOT NULL REFERENCES roadmap_versions(version)
            );
        ''')

    def import_text(self, raw: str, *, source: str) -> ImportResult:
        if not isinstance(source, str) or not source.strip() or len(source) > 500:
            raise StoreError('Roadmap provenance is required')
        if not isinstance(raw, str) or len(raw) > MAX_ROADMAP_BYTES:
            raise StoreError('Roadmap must be text and <= 2 MiB')
        try:
            if len(raw.encode('utf-8')) > MAX_ROADMAP_BYTES:
                raise StoreError('Roadmap UTF-8 input exceeds 2 MiB')
        except UnicodeError as exc:
            raise StoreError('Roadmap contains invalid Unicode') from exc
        doc = _document(raw)
        jobs, specs = _parse_missions(doc)
        canonical = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        with self.store._transaction():
            existing = self.store.db.execute('SELECT version FROM roadmap_versions WHERE digest=?', (digest,)).fetchone()
            if existing is not None:
                return ImportResult(digest, existing['version'], (), tuple(j.id for j in jobs))
            known = {m.id: m for m in self.store.missions()}
            # Never silently drop existing missions (including their checkpoints).
            # Only a separate audited replan may cancel tracked work.
            tracked = {row[0] for row in self.store.db.execute(
                "SELECT m.id FROM mission_specs s JOIN missions m ON m.id=s.id WHERE m.repo=?",
                (doc['repo'],),
            )}
            omitted = tracked - set(specs)
            if omitted:
                raise StoreError('Roadmap omits tracked missions: ' + ','.join(sorted(omitted)))
            to_add = []
            preserved = []
            for job in jobs:
                if job.id in known:
                    previous = self.store.db.execute('SELECT spec_json FROM mission_specs WHERE id=?', (job.id,)).fetchone()
                    if previous is None or previous['spec_json'] != specs[job.id] or known[job.id].repo != job.repo:
                        raise StoreError(f'Conflicting mission revision {job.id}: explicit replan required')
                    preserved.append(job.id)
                else:
                    to_add.append(job)
            combined = (*known.values(), *to_add)
            checked = choose_next(combined, (), now_epoch=int(time.time()), online=False)
            if checked.action == 'BLOCKED' and checked.reason in {
                'Dependency cycle', 'Unknown dependency', 'Duplicate or blank task IDs',
                'Missing or stale completion evidence',
            }:
                raise StoreError(checked.reason)
            now = int(time.time())
            self.store.db.execute('INSERT INTO roadmap_versions(repo,digest,source,imported_epoch) VALUES(?,?,?,?)',
                                  (doc['repo'], digest, source, now))
            version = self.store.db.execute('SELECT last_insert_rowid()').fetchone()[0]
            for job in to_add:
                self.store.db.execute('''INSERT INTO missions
                    (id,repo,operation,dependencies,priority,state,candidate_sha,evidence_sha,
                    external_required,product_decision_required,failures_without_progress,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (job.id,job.repo,job.operation,json.dumps(job.dependencies),job.priority,'READY','','',
                     int(job.external_required),int(job.product_decision_required),0,now))
                self.store.db.execute('INSERT INTO mission_specs(id,spec_json,version) VALUES(?,?,?)',
                                      (job.id,specs[job.id],version))
                self.store._event(job.id,'MISSION_IMPORTED',f'roadmap:{version}')
            self.store._event(None,'ROADMAP_IMPORTED',f'version:{version} sha256:{digest}')
            return ImportResult(digest, version, tuple(j.id for j in to_add), tuple(preserved))

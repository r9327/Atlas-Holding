"""Crash-recoverable Git operation journal with SHA-bound reconciliation.

Only a trusted daemon may invoke this module. A commit is never a Doctor PASS.
PREPARED operations are never replayed automatically after an interruption.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time

from .git_worktree import GitWorktrees, GitSafetyError
from .state_store import StateStore, StoreError

_KEY = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
_SHA = re.compile(r'^[a-f0-9]{40}$')


@dataclass(frozen=True)
class Operation:
    mission_id: str
    key: str
    kind: str
    intent_digest: str
    intent_json: str
    base_sha: str
    status: str
    result_sha: str


class GitJournal:
    """Transactional operation journal tied to an exclusive daemon lease."""

    def __init__(self, store: StateStore, git: GitWorktrees, *, lease_owner: str):
        self.store = store
        self.git = git
        self.lease_owner = lease_owner
        store.db.execute("""CREATE TABLE IF NOT EXISTS git_operations (
            mission_id TEXT NOT NULL REFERENCES missions(id),
            operation_key TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('CREATE', 'COMMIT')),
            intent_digest TEXT NOT NULL,
            intent_json TEXT NOT NULL,
            base_sha TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('PREPARED', 'APPLIED', 'INSPECT_REQUIRED')),
            result_sha TEXT NOT NULL DEFAULT '',
            updated_epoch INTEGER NOT NULL,
            PRIMARY KEY (mission_id, operation_key)
        )""")

    @staticmethod
    def digest(intent: object) -> str:
        return hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(',', ':'),
                                         ensure_ascii=False).encode('utf-8')).hexdigest()

    @staticmethod
    def _entry(row) -> Operation:
        return Operation(row['mission_id'], row['operation_key'], row['kind'],
                         row['intent_digest'], row['intent_json'], row['base_sha'],
                         row['state'], row['result_sha'])

    def _load(self, mission_id: str, key: str) -> Operation | None:
        row = self.store.db.execute('SELECT * FROM git_operations WHERE mission_id=? AND operation_key=?',
                                    (mission_id, key)).fetchone()
        return self._entry(row) if row else None

    def prepare(self, mission_id: str, key: str, kind: str,
                *, intent: object, base_sha: str) -> Operation:
        """Record intent BEFORE Git mutation; any conflicting identity is rejected."""
        if not _KEY.fullmatch(key) or kind not in {'CREATE', 'COMMIT'} or not _SHA.fullmatch(base_sha):
            raise StoreError('Invalid Git operation identity or base commit')
        encoded = json.dumps(intent, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        digest = self.digest(intent)
        with self.store._transaction():
            self.store._assert_daemon_fence(self.lease_owner)
            old = self._load(mission_id, key)
            if old is not None:
                if (old.kind, old.intent_digest, old.base_sha) != (kind, digest, base_sha):
                    raise StoreError('Git operation key reused with different intent')
                return old
            row = self.store.db.execute('SELECT state FROM missions WHERE id=? AND repo=?',
                                        (mission_id, self.git.repository)).fetchone()
            if row is None or row['state'] != 'RUNNING':
                raise StoreError('Git operation requires a claimed mission for this repository')
            pending = self.store.db.execute("""SELECT operation_key FROM git_operations
                WHERE mission_id=? AND state!='APPLIED' LIMIT 1""", (mission_id,)).fetchone()
            if pending:
                raise StoreError('Previous Git operation requires reconciliation')
            self.store.db.execute("""INSERT INTO git_operations
                (mission_id,operation_key,kind,intent_digest,intent_json,base_sha,state,updated_epoch)
                VALUES (?,?,?,?,?,?,'PREPARED',?)""",
                (mission_id, key, kind, digest, encoded, base_sha, int(time.time())))
            self.store._event(mission_id, 'GIT_INTENT_PREPARED', f'{kind}:{key}:{base_sha}')
            return self._load(mission_id, key)

    def applied(self, mission_id: str, key: str, result_sha: str) -> Operation:
        """Persist a Git result after independent local verification."""
        if not _SHA.fullmatch(result_sha):
            raise StoreError('Invalid resulting Git SHA')
        with self.store._transaction():
            self.store._assert_daemon_fence(self.lease_owner)
            old = self._load(mission_id, key)
            if old is None:
                raise StoreError('No prepared Git operation')
            if old.status == 'APPLIED':
                if old.result_sha != result_sha:
                    raise StoreError('Conflicting operation result')
                return old
            if old.status != 'PREPARED':
                raise StoreError('Inspection is required before recording result')
            self.store.db.execute("""UPDATE git_operations SET state='APPLIED', result_sha=?,
                updated_epoch=? WHERE mission_id=? AND operation_key=?""",
                (result_sha, int(time.time()), mission_id, key))
            self.store._event(mission_id, 'GIT_OPERATION_APPLIED', f'{old.kind}:{key}:{result_sha}')
            return self._load(mission_id, key)

    def inspect_recovery(self, mission_id: str, key: str) -> str:
        """APPLIED / NOT_APPLIED / INSPECT_REQUIRED; NEVER automatically rerun."""
        with self.store._transaction():
            self.store._assert_daemon_fence(self.lease_owner)
            old = self._load(mission_id, key)
            if old is None:
                raise StoreError('Unknown Git operation')
            result = self._verify(old)
            if old.status == 'APPLIED':
                return result
            if result == 'APPLIED':
                sha = self.git.inspect(mission_id).base_sha
                if old.kind == 'CREATE':
                    sha = old.base_sha
                self.store.db.execute("""UPDATE git_operations SET state='APPLIED',
                    result_sha=?,updated_epoch=? WHERE mission_id=? AND operation_key=?""",
                    (sha, int(time.time()), mission_id, key))
                self.store._event(mission_id, 'GIT_OPERATION_RECONCILED', f'{key}:{sha}')
            elif result == 'INSPECT_REQUIRED':
                self.store.db.execute("""UPDATE git_operations SET state='INSPECT_REQUIRED',
                    updated_epoch=? WHERE mission_id=? AND operation_key=?""",
                    (int(time.time()), mission_id, key))
                self.store._event(mission_id, 'GIT_OPERATION_UNCERTAIN', key)
            return result

    def _verify(self, old: Operation) -> str:
        try:
            tree = self.git.inspect(old.mission_id)
        except GitSafetyError:
            return 'INSPECT_REQUIRED'
        head = tree.base_sha
        if old.status == 'APPLIED':
            if old.kind == 'CREATE':
                return 'APPLIED'
            try:
                self.git._git('merge-base', '--is-ancestor', old.result_sha, 'HEAD', cwd=tree.path)
                return 'APPLIED'
            except GitSafetyError:
                return 'INSPECT_REQUIRED'
        if old.kind == 'CREATE':
            if head == old.base_sha and self.git.inspect_recovery(old.mission_id):
                return 'APPLIED'
            return 'INSPECT_REQUIRED'
        if head == old.base_sha:
            return 'NOT_APPLIED'
        try:
            parent = self.git._git('rev-parse', 'HEAD^', cwd=tree.path)
            message = self.git._git('log', '-1', '--format=%B', cwd=tree.path)
            trailer = f'Holding-Operation: {old.key}'
            intent = json.loads(old.intent_json)
            paths = set(intent['paths'])
            actual = {p for p in self.git._git('diff-tree', '--no-commit-id',
                        '--name-only', '-r', '-z', 'HEAD', cwd=tree.path).split('\\0') if p}
            if (parent == old.base_sha and message.rstrip().endswith(trailer)
                    and message.rstrip() == intent['message'].rstrip() + '\\n\\n' + trailer
                    and actual == paths):
                return 'APPLIED'
        except GitSafetyError:
            pass
        return 'INSPECT_REQUIRED'

    def get(self, mission_id: str, key: str) -> Operation | None:
        return self._load(mission_id, key)

    def pending(self, mission_id: str) -> tuple[Operation, ...]:
        return tuple(self._entry(row) for row in self.store.db.execute("""
            SELECT * FROM git_operations WHERE mission_id=? AND state!='APPLIED'
            ORDER BY updated_epoch, operation_key""", (mission_id,)))

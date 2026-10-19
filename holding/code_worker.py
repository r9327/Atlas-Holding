"""Make a code patch from a local agent, but keep Git writes in the supervisor.

Agent output is UNTRUSTED DATA, never command lines or proof of passing tests.
The configured executable must be ACL-isolated before production deployment.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from .autonomy import Mission
from .executor import AuthorizedGit
from .git_worktree import GitSafetyError
from .process_agent import LocalProcessAgent, AgentProcessError

_SAFE_PATH = re.compile(r'^[A-Za-z0-9_./-]{1,160}$')
_FORBIDDEN_PARTS = {'.git', '.github', '.env', 'secrets', 'tokens', 'credentials'}


class CodePlanError(RuntimeError):
    pass


class LocalCodeWorker:
    """Call a preinstalled local agent, validate patch files, commit via leased Git."""

    def __init__(self, authorized_git: AuthorizedGit, agent: LocalProcessAgent,
                 *, health_directory: Path):
        self.git = authorized_git
        self.agent = agent
        self.health_directory = health_directory.resolve(strict=True)
        if not self.health_directory.is_dir() or self.health_directory.is_symlink():
            raise CodePlanError('Health-check directory must be a regular directory')
        self._last_health: tuple[float, bool] | None = None

    def ready(self) -> bool:
        now = time.monotonic()
        if self._last_health is not None and now-self._last_health[0] < 15:
            return self._last_health[1]
        try:
            ok = self.agent.request('health', {}, cwd=self.health_directory).get('agent_ready') is True
        except (AgentProcessError, OSError):
            ok = False
        self._last_health = (now, ok)
        return ok

    @staticmethod
    def _files(plan: dict, root: Path) -> list[tuple[str, bytes]]:
        if not isinstance(plan, dict) or set(plan) != {'files', 'commit_message'}:
            raise CodePlanError('Agent patch has unknown or missing fields')
        files, message = plan['files'], plan['commit_message']
        if (not isinstance(files, list) or not 1 <= len(files) <= 12 or
                not isinstance(message, str) or not 5 <= len(message) <= 160 or
                '\n' in message or '\r' in message):
            raise CodePlanError('Invalid patch size or commit message')
        result, used = [], set()
        for change in files:
            if not isinstance(change, dict) or set(change) != {'path', 'content'}:
                raise CodePlanError('Unexpected patch format')
            filename, content = change['path'], change['content']
            if not isinstance(filename, str) or not _SAFE_PATH.fullmatch(filename):
                raise CodePlanError('Unsafe patch filename')
            path = Path(filename)
            if (path.is_absolute() or path.as_posix() != filename or
                    any(part.lower() in _FORBIDDEN_PARTS or part in ('.', '..', '') or
                        part.startswith('.') for part in path.parts)):
                raise CodePlanError('Forbidden patch path')
            if filename.lower() in used:
                raise CodePlanError('Duplicate/case-conflicting patch filename')
            used.add(filename.lower())
            if not isinstance(content, str):
                raise CodePlanError('Patch content must be UTF-8 text')
            data = content.encode('utf-8')
            if len(data) > 128 * 1024:
                raise CodePlanError('Patch file exceeds 128 KiB')
            destination = root / path
            if destination.is_symlink() or destination.is_dir():
                raise CodePlanError('Patch must target a normal file')
            for parent in destination.parents:
                if parent == root:
                    break
                if parent.is_symlink() or not parent.resolve().is_relative_to(root):
                    raise CodePlanError('Symlink escape in patch parent')
            if not destination.resolve().is_relative_to(root):
                raise CodePlanError('Patch escapes worktree')
            result.append((filename, data))
        if sum(len(data) for _, data in result) > 256 * 1024:
            raise CodePlanError('Patch exceeds total size limit')
        return result

    def _acceptance(self, mission: Mission) -> tuple[str, ...]:
        """Never ask an LLM to code without the original roadmap requirements."""
        table = self.git.store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mission_specs'"
        ).fetchone()
        if table is None:
            raise CodePlanError('No imported roadmap specification exists')
        row = self.git.store.db.execute(
            "SELECT spec_json FROM mission_specs WHERE id=?", (mission.id,)
        ).fetchone()
        if row is None:
            raise CodePlanError('Mission lacks immutable roadmap acceptance criteria')
        try:
            spec = json.loads(row['spec_json'])
        except (ValueError, TypeError) as exc:
            raise CodePlanError('Damaged roadmap mission specification') from exc
        criteria = spec.get('acceptance')
        if (spec.get('id') != mission.id or spec.get('operation') != mission.operation or
                not isinstance(criteria, list) or not criteria or
                any(not isinstance(item, str) or not item.strip() for item in criteria)):
            raise CodePlanError('Mission and roadmap specification disagree')
        return tuple(criteria)

    def __call__(self, mission: Mission) -> str:
        if mission.operation != 'code':
            raise CodePlanError('This worker handles only code missions')
        criteria = self._acceptance(mission)
        if not self.ready():
            raise AgentProcessError('Agent health check failed')
        # Supervisor, not the model, chooses/reuses the exact worktree.
        worktree = self.git.create(mission.id)
        if self.git.git._git('status', '--porcelain', cwd=worktree.path):
            raise CodePlanError('Worktree must be clean before agent starts')
        request = {'mission_id': mission.id, 'repository': mission.repo,
                   'branch': worktree.branch, 'base_sha': worktree.base_sha,
                   'dependencies': list(mission.dependencies),
                   'acceptance': list(criteria)}
        response = self.agent.request('plan_patch', request, cwd=worktree.path)
        # The child is not trusted to edit files directly. Any direct changes
        # are quarantined by the normal worker failure/recovery path.
        if self.git.git._git('status', '--porcelain', cwd=worktree.path):
            raise CodePlanError('Agent modified worktree instead of proposing a patch')
        self.git.git.inspect_canonical()
        if set(response) != {'protocol', 'action', 'status', 'plan'}:
            raise CodePlanError('Agent response contains unsupported actions')
        plan = response['plan']
        files = self._files(plan, worktree.path)
        digest = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        # Validation finishes before the first write. Failures after writes are
        # quarantined; never reset an uncertain worktree or retry a commit blindly.
        for path, data in files:
            destination = worktree.path / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix='.holding-', dir=destination.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, destination)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        self.git.lease.assert_owned()
        return self.git.commit(mission.id, tuple(p for p, _ in files),
                               plan['commit_message'], operation_key='patch-' + digest[:32])

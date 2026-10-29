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
from dataclasses import asdict
from .executor import AuthorizedGit
from .existing_code import inspect_existing_code, ExistingCodeError
from .existing_consumers import inspect_static_import_consumers
from .github_pr_inventory import OpenPrInventory
from .github_full_evidence import GitHubEvidenceError
from .git_worktree import GitSafetyError
from .process_agent import LocalProcessAgent, AgentProcessError, AgentInterrupted

_SAFE_PATH = re.compile(r'^[A-Za-z0-9_./-]{1,160}$')
_FORBIDDEN_PARTS = {'.git', '.github', '.env', 'secrets', 'tokens', 'credentials'}
# Guardrails are outside model control. The coding role cannot modify
# verification, credentials, permissions, deployment gates or independent tests.
_PROTECTED_TERMS = ('doctor', 'security', 'permission', 'authorization',
                    'authentication', 'credential', 'signature', 'certif',
                    'workflow', 'policy', 'secrets', 'token', 'gate')
_PROTECTED_COMPONENTS = {'tests', 'test', '.github', 'holding', 'ci', 'certification'}


def _protected_patch_path(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    return (any(part in _PROTECTED_COMPONENTS for part in parts) or
            any(term in part for part in parts for term in _PROTECTED_TERMS) or
            any(part.startswith(('test_', 'ci_')) or part in ('conftest.py', 'tox.ini') for part in parts))



class CodePlanError(RuntimeError):
    pass


class LocalCodeWorker:
    """Call a preinstalled local agent, validate patch files, commit via leased Git."""

    def __init__(self, authorized_git: AuthorizedGit, agent: LocalProcessAgent,
                 *, health_directory: Path, github_pr_observer=None):
        if (github_pr_observer is not None
                and not callable(getattr(github_pr_observer, "observe_open_prs", None))):
            raise CodePlanError("Invalid trusted GitHub PR observer")
        self.github_pr_observer = github_pr_observer
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
            if _protected_patch_path(path):
                raise CodePlanError('Agent is not authorized to edit protected test or security paths')
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

    def _mission_context(self, mission: Mission) -> tuple[tuple[str, ...], str | None]:
        """Recover exact persisted acceptance and global user project direction."""
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
        if not isinstance(spec, dict):
            raise CodePlanError('Invalid roadmap mission specification')
        criteria = spec.get('acceptance')
        if (spec.get('id') != mission.id or spec.get('operation') != mission.operation or
                not isinstance(criteria, list) or not criteria or
                any(not isinstance(item, str) or not item.strip() for item in criteria)):
            raise CodePlanError('Mission and roadmap specification disagree')
        goal = spec.get('project_goal')
        if goal is not None and (not isinstance(goal, str) or not goal.strip()
                                 or len(goal.encode('utf-8')) > 4096):
            raise CodePlanError('Invalid stored project direction')
        return tuple(criteria), goal

    def __call__(self, mission: Mission) -> str:
        if mission.operation != 'code':
            raise CodePlanError('This worker handles only code missions')
        criteria, project_goal = self._mission_context(mission)
        # Verify BOTH standing grants before spending CPU/VRAM on generation or
        # creating a worktree. A 'code'-only grant is never commit permission.
        self.git._require(mission.id, 'code', 'commit')
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
        if project_goal is not None:
            request['project_goal'] = project_goal
        # The exact baseline tree is observed BEFORE model generation. This
        # context is advisory and cannot waive independent QA or skip a task.
        if self.git.git._git('rev-parse', 'HEAD', cwd=worktree.path) != worktree.base_sha:
            raise CodePlanError('Worktree HEAD moved before existing-code discovery')
        def read_git(*args: str) -> str:
            return self.git.git._git(*args, cwd=worktree.path)
        try:
            existing = inspect_existing_code(
                read_git, repository_sha=worktree.base_sha,
                acceptance=criteria,
            )
        except ExistingCodeError as exc:
            raise CodePlanError('Cannot establish exact-SHA existing-code inventory') from exc
        request['existing_code'] = asdict(existing)
        # A bounded follow-up search for real static imports; still advisory.
        # Never infer missing consumers, active call paths, or compatibility.
        if existing.candidates:
            try:
                consumers = inspect_static_import_consumers(
                    read_git, repository_sha=worktree.base_sha,
                    candidate_paths=tuple(
                        candidate.path for candidate in existing.candidates
                        if candidate.path.endswith('.py')),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise CodePlanError(
                    'Cannot validate static consumer inventory') from exc
            request['existing_consumers'] = asdict(consumers)
        request['github_pr_observation'] = 'NOT_CHECKED'
        if self.github_pr_observer is not None:
            try:
                report = self.github_pr_observer.observe_open_prs(mission.repo)
            except GitHubEvidenceError:
                # No network/credential verification is NOT evidence of no PRs.
                request['github_pr_observation'] = 'UNAVAILABLE'
            else:
                if (not isinstance(report, OpenPrInventory)
                        or report.repository != mission.repo
                        or report.can_certify or report.can_skip_implementation):
                    raise CodePlanError('GitHub PR observation is untrusted')
                request['github_pr_observation'] = 'OBSERVED'
                request['github_open_prs'] = asdict(report)
        # A failing independent QA SHA gives the coding agent explicit
        # correction context without passing it credentials, signatures,
        # arbitrary test commands, or a self-certification privilege.
        table = self.git.store.db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='qa_repair_attempts'").fetchone()[0]
        if table:
            prior = self.git.store.db.execute(
                "SELECT failed_sha FROM qa_repair_attempts WHERE mission_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (mission.id,)).fetchone()
            if prior is not None:
                request['previous_failed_candidate_sha'] = prior['failed_sha']
                request['repair_objective'] = (
                    'Previous candidate failed independent QA; identify root '
                    'cause and deliver a new tested correction SHA')
        response = self.agent.request('plan_patch', request, cwd=worktree.path)
        peak_bytes = self.agent.last_job_peak_bytes
        if type(peak_bytes) is int and peak_bytes >= 0:
            # Advisory metrics only. The Ollama inference server, supervisor
            # and GUI are separate processes and are not included here.
            with self.git.store._transaction():
                self.git.store._assert_daemon_fence(self.git.lease.owner)
                self.git.store._event(
                    mission.id, 'CODER_ADAPTER_JOB_PEAK_BYTES', str(peak_bytes))
        # The child is not trusted to edit files directly. Any direct changes
        # are quarantined by the normal worker failure/recovery path.
        if self.git.git._git('status', '--porcelain', cwd=worktree.path):
            raise CodePlanError('Agent modified worktree instead of proposing a patch')
        self.git.git.inspect_canonical()
        if set(response) != {'protocol', 'action', 'status', 'plan'}:
            raise CodePlanError('Agent response contains unsupported actions')
        plan = response['plan']
        files = self._files(plan, worktree.path)
        # The operator may have pressed Pause/STOP while the local model
        # generated its plan. No writes, even temporary files, after that.
        if self.git.store.background_suspended():
            raise AgentInterrupted('Controller suspended before patch application')
        self.git._require(mission.id, 'code', 'commit')
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

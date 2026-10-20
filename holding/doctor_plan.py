"""Read-only Doctor Atlas exact-SHA plan, never certification evidence.

Doctor itself documents that local/scoped reports are advisory and that GitHub
FULL/policy gates remain authoritative. This adapter MUST NOT call
StateStore.register_test_verdict, ProofVerifier.apply, or mark a mission DONE.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Callable
import re
import signal
import subprocess
import tempfile
import time

from .git_worktree import GitWorktrees, GitSafetyError
from .process_agent import LocalProcessAgent
from .windows_job import CREATE_SUSPENDED

_SHA = re.compile(r'^[0-9a-f]{40}$')
_MAX_REPORT = 1024 * 1024


class DoctorPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class DoctorAdvice:
    candidate_sha: str
    status: str
    report_sha256: str
    advisory_only: bool = True



def _run_bounded_planning(command: list[str], *, cwd: Path,
                          timeout_seconds: int,
                          should_stop: Callable[[], bool] | None = None) -> tuple[int, bytes]:
    """Execute plan-only Doctor with bounded output and a contained process tree.

    A timeout on subprocess.run() kills its direct child only: helpers may
    survive and keep reading files. Use a POSIX process group or a Windows Job
    Object, and never inherit Git credentials or proxy configuration.
    """
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR",
                                      "TEMP", "TMP", "TMPDIR", "LANG",
                                      "PYTHONIOENCODING"}}
    environment.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1",
                       GIT_TERMINAL_PROMPT="0", NO_PROXY="127.0.0.1,localhost")
    proc = None
    job = None
    with tempfile.TemporaryFile() as output:
        try:
            if os.name == "nt":
                from .windows_job import WindowsJob
                job = WindowsJob()
            if should_stop is not None and should_stop():
                raise DoctorPlanError("Doctor planning interrupted by controller")
            started = time.monotonic()
            proc = subprocess.Popen(
                command, cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.DEVNULL,
                env=environment, shell=False,
                start_new_session=(os.name != "nt"),
                creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                               | CREATE_SUSPENDED if os.name == "nt" else 0))
            if job is not None:
                job.attach_suspended(proc)
            while proc.poll() is None:
                if should_stop is not None and should_stop():
                    raise DoctorPlanError("Doctor planning interrupted by controller")
                if time.monotonic() - started > timeout_seconds:
                    raise DoctorPlanError("Doctor planning exceeded deadline")
                if os.fstat(output.fileno()).st_size > _MAX_REPORT:
                    raise DoctorPlanError("Doctor report exceeds configured limit")
                time.sleep(.05)
            if os.fstat(output.fileno()).st_size > _MAX_REPORT:
                raise DoctorPlanError("Doctor report exceeds configured limit")
            output.seek(0)
            raw = output.read(_MAX_REPORT + 1)
            if len(raw) > _MAX_REPORT:
                raise DoctorPlanError("Doctor report exceeds configured limit")
            return proc.returncode, raw
        except BaseException:
            if job is not None:
                try:
                    job.terminate()
                except Exception:
                    pass
                finally:
                    if proc is not None and proc.poll() is None:
                        LocalProcessAgent._stop_tree(proc)
            elif proc is not None:
                LocalProcessAgent._stop_tree(proc)
            raise
        finally:
            if job is not None:
                job.close()
                LocalProcessAgent._reap_after_job_close(proc)
            elif os.name != "nt" and proc is not None:
                # The parent may have returned after spawning a helper.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

class DoctorPlanner:
    """Run the repository's existing Doctor CLI with a fixed, plan-only argv."""

    def __init__(self, git: GitWorktrees, interpreter: Path, *, timeout_seconds: int = 90):
        if (not interpreter.is_absolute() or not interpreter.is_file() or
                interpreter.is_symlink()):
            raise DoctorPlanError('Trusted Python interpreter must be a regular absolute file')
        if not 5 <= timeout_seconds <= 300:
            raise DoctorPlanError('Unsafe Doctor planning timeout')
        self.git = git
        self.interpreter = interpreter
        self.timeout_seconds = timeout_seconds

    def plan(self, mission_id: str, candidate_sha: str, *,
             should_stop: Callable[[], bool] | None = None) -> DoctorAdvice:
        if not _SHA.fullmatch(candidate_sha):
            raise DoctorPlanError('Exact candidate SHA required')
        work = self.git.inspect(mission_id)
        if work.base_sha != candidate_sha:
            raise DoctorPlanError('Candidate SHA has changed since checkpoint')
        entrypoint = work.path / 'tools' / 'atlas_doctor.py'
        if not entrypoint.is_file() or entrypoint.is_symlink():
            raise DoctorPlanError('Doctor Atlas entrypoint not available')
        if self.git._git('status', '--porcelain', cwd=work.path):
            raise DoctorPlanError('Doctor requires a clean exact-SHA worktree')
        main_sha = self.git._git('rev-parse', '--verify',
                                 'refs/heads/main^{commit}', cwd=work.path)
        if not _SHA.fullmatch(main_sha):
            raise DoctorPlanError('Invalid main comparison SHA')
        try:
            self.git._git('merge-base', '--is-ancestor', main_sha, candidate_sha,
                          cwd=work.path)
        except GitSafetyError as exc:
            raise DoctorPlanError('Candidate has an outdated or unrelated base') from exc
        # Doctor cannot independently audit changes to its own trust boundary.
        # Those diffs must use protected external certification instead.
        protected = (
            '.github/workflows', 'tools/atlas_doctor.py', 'tools/atlas_doctor_lib',
            'tools/doctor_certification.py', 'tools/doctor_policy_authority.py',
            'tools/doctor_github_provenance.py', 'tools/atlas_integrity.py',
            'tools/atlas_meta_integrity.py', 'tools/ci_scope_gate.py',
            'tools/ci_required_certification.py', 'tools/phase_certification_verdict.py',
            'PHASE_CERTIFICATION.md', 'ZERO_TRUST_RULES.md',
            'GITHUB_PROTECTION.md', 'DEVELOPMENT_GUARDRAILS.md',
        )
        changed = self.git._git('diff', '--name-only',
                                f'{main_sha}..{candidate_sha}', '--', *protected,
                                cwd=work.path)
        if changed:
            raise DoctorPlanError('Doctor or certification policy changed; independent review required')
        # Fixed arguments: no --run-tests, no --install, no external calls.
        cmd = [str(self.interpreter), '-B', '-m', 'tools.atlas_doctor',
               'certify', '--base-ref', main_sha,
               '--expected-sha', candidate_sha, '--json']
        try:
            options = {"cwd": work.path, "timeout_seconds": self.timeout_seconds}
            if should_stop is not None:
                options["should_stop"] = should_stop
            exit_code, raw = _run_bounded_planning(cmd, **options)
        except (OSError, subprocess.SubprocessError) as exc:
            raise DoctorPlanError('Doctor planning process failed') from exc
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise DoctorPlanError('Doctor did not return one JSON object') from exc
        if not isinstance(payload, dict):
            raise DoctorPlanError('Doctor report must be a JSON object')
        if payload.get('candidate_sha') != candidate_sha:
            raise DoctorPlanError('Doctor report references a different SHA')
        if not isinstance(payload.get('execution'), dict) or payload['execution'].get('status') not in ('NOT_RUN', 'FULL_REQUIRED_NOT_DISPATCHED'):
            raise DoctorPlanError('Doctor unexpectedly ran tests or claimed execution')
        if any(payload.get(name) is True for name in (
                'certified', 'phase_certified', 'release_certified',
                'merge_authorized', 'full_suite_waived')):
            raise DoctorPlanError('Unauthorized certification claim in Doctor report')
        if exit_code not in (0, 2) or not isinstance(payload.get('status'), str):
            raise DoctorPlanError('Doctor failed without a valid advisory report')
        if self.git._git('status', '--porcelain', cwd=work.path):
            raise DoctorPlanError('Doctor modified the worktree while planning')
        return DoctorAdvice(candidate_sha, payload['status'],
                            hashlib.sha256(raw).hexdigest())

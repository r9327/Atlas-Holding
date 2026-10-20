"""Run Atlas Doctor's *scoped* test contract for an exact Git SHA.

This is advisory evidence only. It CANNOT sign a QA PASS, mark a mission DONE,
or waive protected GitHub FULL certification. A separately privileged witness
must verify official CI/Doctor evidence before final promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Callable
import re
import subprocess
import tempfile
import time

from .process_agent import LocalProcessAgent
from .schema import SCOPED_QA_SCHEMA, apply_schema
from .windows_job import CREATE_SUSPENDED

from .doctor_plan import DoctorPlanner, DoctorPlanError
from .git_worktree import GitWorktrees, GitSafetyError
from .state_store import StateStore, StoreError

_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_REPORT = 2 * 1024 * 1024


class ScopedQaError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScopedQaResult:
    candidate_sha: str
    base_sha: str
    status: str
    modules: tuple[str, ...]
    report_sha256: str
    advisory_only: bool = True


def _run_limited_doctor(command: list[str], *, cwd: Path,
                        timeout_seconds: int,
                        should_stop: Callable[[], bool] | None = None) -> tuple[int, bytes]:
    """Time/output-bound process tree: never buffer arbitrary test output in RAM."""
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP",
                                      "TMP", "TMPDIR", "LANG", "PYTHONIOENCODING"}}
    environment.update(PYTHONNOUSERSITE="1", PYTHONUTF8="1",
                       GIT_TERMINAL_PROMPT="0", NO_PROXY="127.0.0.1,localhost")
    with tempfile.TemporaryFile() as output:
        proc = None
        job = None
        try:
            if os.name == "nt":
                from .windows_job import WindowsJob
                job = WindowsJob()
            if should_stop is not None and should_stop():
                raise ScopedQaError("Scoped Doctor interrupted by controller")
            start = time.monotonic()
            proc = subprocess.Popen(
                command, cwd=cwd, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.DEVNULL, env=environment,
                shell=False, start_new_session=(os.name != "nt"),
                creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                               | CREATE_SUSPENDED if os.name == "nt" else 0))
            if job is not None:
                job.attach_suspended(proc)
            while proc.poll() is None:
                if should_stop is not None and should_stop():
                    raise ScopedQaError("Scoped Doctor interrupted by controller")
                if time.monotonic() - start > timeout_seconds:
                    raise ScopedQaError("Scoped Doctor timed out")
                if os.fstat(output.fileno()).st_size > _MAX_REPORT:
                    raise ScopedQaError("Scoped Doctor output exceeded 2 MiB")
                time.sleep(0.05)
            if os.fstat(output.fileno()).st_size > _MAX_REPORT:
                raise ScopedQaError("Scoped Doctor output exceeded 2 MiB")
            output.seek(0)
            raw = output.read(_MAX_REPORT + 1)
            if len(raw) > _MAX_REPORT:
                raise ScopedQaError("Scoped Doctor output exceeded 2 MiB")
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
                # The root process may have exited after spawning helpers.
                import signal
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


class ScopedDoctorRunner:
    """Trusted supervisor runs the repository's fixed, existing test selector.

    The agent never chooses shell args, test modules, or reports. Running any
    code from a candidate checkout requires a separate low-privilege OS
    identity with no signing key, credentials or user data. This class alone
    is NOT a Windows sandbox and must not be used as production attestation.
    """

    def __init__(self, git: GitWorktrees, interpreter: Path, *,
                 timeout_seconds: int = 900):
        if (not interpreter.is_absolute() or not interpreter.is_file()
                or interpreter.is_symlink()):
            raise ScopedQaError("Trusted interpreter must be a real absolute file")
        if not 30 <= timeout_seconds <= 3600:
            raise ScopedQaError("Scoped test timeout must be 30..3600 seconds")
        self.git = git
        self.interpreter = interpreter
        self.timeout_seconds = timeout_seconds

    def run(self, mission_id: str, candidate_sha: str, *,
            should_stop: Callable[[], bool] | None = None) -> ScopedQaResult:
        if not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha):
            raise ScopedQaError("An exact lowercase candidate SHA is required")
        # Reuse existing Doctor planning gates: trust-boundary modifications,
        # stale comparison base, dirty checkouts and missing Doctor are rejected.
        try:
            preflight = DoctorPlanner(self.git, self.interpreter)
            if should_stop is None:
                advice = preflight.plan(mission_id, candidate_sha)
            else:
                advice = preflight.plan(
                    mission_id, candidate_sha, should_stop=should_stop)
        except (DoctorPlanError, GitSafetyError) as exc:
            raise ScopedQaError("Trusted Doctor preflight failed") from exc
        tree = self.git.inspect(mission_id)
        if tree.base_sha != candidate_sha:
            raise ScopedQaError("Candidate changed after preflight")
        base_sha = self.git._git("rev-parse", "--verify",
                                 "refs/heads/main^{commit}", cwd=tree.path)
        if not _SHA.fullmatch(base_sha):
            raise ScopedQaError("Unverified base SHA")
        if advice.status != "READY_TO_RUN_SCOPED":
            status = ("FULL_REQUIRED" if advice.status == "FULL_REQUIRED"
                      else "SCOPED_PLAN_BLOCKED")
            return ScopedQaResult(candidate_sha, base_sha, status, (),
                                  advice.report_sha256)
        cmd = [str(self.interpreter), "-B", "-m", "tools.doctor_certification",
               "--base-ref", base_sha, "--expected-sha", candidate_sha,
               "--run"]
        # The caller must be a separately sandboxed/unprivileged supervisor.
        # Keep stdout and descendants bounded, even for malicious test code.
        try:
            options = {"cwd": tree.path, "timeout_seconds": self.timeout_seconds}
            if should_stop is not None:
                options["should_stop"] = should_stop
            return_code, raw = _run_limited_doctor(cmd, **options)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ScopedQaError("Scoped Doctor execution failed") from exc
        if not raw or len(raw) > _MAX_REPORT:
            raise ScopedQaError("Scoped Doctor report missing or oversized")
        try:
            report = json.loads(raw)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ScopedQaError("Invalid scoped Doctor JSON") from exc
        if not isinstance(report, dict):
            raise ScopedQaError("Scoped Doctor result must be an object")
        execution = report.get("execution")
        if not isinstance(execution, dict):
            raise ScopedQaError("Missing scoped execution evidence")
        if report.get("candidate_sha") != candidate_sha or report.get("base_sha") != base_sha:
            raise ScopedQaError("Scoped QA report references the wrong Git SHA")
        if any(report.get(field) is True for field in (
                "certified", "phase_certified", "release_certified",
                "merge_authorized", "full_suite_waived")):
            raise ScopedQaError("Untrusted report claims full certification")
        if any(execution.get(field) is True for field in ("certified", "phase_certified")):
            raise ScopedQaError("Execution claims unauthorized certification")
        # No scoped report ever grants DONE. Even a clean exit alone is NOT PASS.
        valid_modules = (
            isinstance(report.get("test_modules"), list)
            and bool(report["test_modules"])
            and all(isinstance(m, str) and m for m in report["test_modules"])
            and execution.get("executed_modules") == report["test_modules"]
        )
        safe_clean = not bool(self.git._git("status", "--porcelain",
                                             cwd=tree.path))
        same_head = self.git.inspect(mission_id).base_sha == candidate_sha
        passed = (return_code == 0
                  and report.get("status") == "READY_TO_RUN_SCOPED"
                  and execution.get("status") == "SCOPED_TESTS_PASS"
                  and execution.get("exit_code") == 0
                  and valid_modules and safe_clean and same_head)
        return ScopedQaResult(candidate_sha, base_sha,
                              "SCOPED_TESTS_PASS" if passed else "SCOPED_TESTS_FAIL",
                              tuple(report["test_modules"]) if valid_modules else (),
                              hashlib.sha256(raw).hexdigest())

    def run_pending(self, store: StateStore, mission_id: str) -> ScopedQaResult:
        """Persist one advisory for Control Center. No state transition to DONE."""
        if store.background_suspended():
            raise StoreError("Holding paused or stopped; scoped QA disabled")
        row = store.db.execute(
            "SELECT candidate_sha FROM missions WHERE id=? AND repo=? "
            "AND state='QA_PENDING'", (mission_id, self.git.repository)).fetchone()
        if row is None or not _SHA.fullmatch(row["candidate_sha"]):
            raise StoreError("Only a QA_PENDING mission with real SHA can be checked")
        # A code grant is not permission to execute tests. This distinction
        # is enforced by the trusted supervisor, never a prompt or roadmap.
        import time
        if not any(grant.allows(self.git.repository, "local_test", int(time.time()))
                   for grant in store._grants()):
            raise StoreError("No active exact-repository local_test permission")
        result = self.run(
            mission_id, row["candidate_sha"],
            should_stop=store.background_suspended)
        with store._transaction():
            if store.background_suspended():
                raise StoreError("Holding paused or stopped during scoped QA")
            if not any(grant.allows(self.git.repository, "local_test", int(time.time()))
                       for grant in store._grants()):
                raise StoreError("Scoped QA grant revoked during execution")
            current = store.db.execute(
                "SELECT candidate_sha FROM missions WHERE id=? AND repo=? "
                "AND state='QA_PENDING'", (mission_id, self.git.repository)).fetchone()
            if current is None or current["candidate_sha"] != result.candidate_sha:
                raise StoreError("QA result became stale during execution")
            apply_schema(store.db, SCOPED_QA_SCHEMA[:1])
            store.db.execute("""INSERT INTO scoped_qa_advisories
                (mission_id,candidate_sha,status,base_sha,report_sha256,checked_epoch)
                VALUES(?,?,?,?,?,strftime('%s','now'))
                ON CONFLICT(mission_id) DO UPDATE SET
                candidate_sha=excluded.candidate_sha,status=excluded.status,
                base_sha=excluded.base_sha,report_sha256=excluded.report_sha256,
                checked_epoch=excluded.checked_epoch""",
                (mission_id, result.candidate_sha, result.status,
                 result.base_sha, result.report_sha256))
            store._event(mission_id, "SCOPED_QA_ADVISORY",
                         f"{result.status}:{result.candidate_sha}")
        return result

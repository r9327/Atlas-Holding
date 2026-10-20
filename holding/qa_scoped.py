"""Run Atlas Doctor's *scoped* test contract for an exact Git SHA.

This is advisory evidence only. It CANNOT sign a QA PASS, mark a mission DONE,
or waive protected GitHub FULL certification. A separately privileged witness
must verify official CI/Doctor evidence before final promotion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

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

    def run(self, mission_id: str, candidate_sha: str) -> ScopedQaResult:
        if not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha):
            raise ScopedQaError("An exact lowercase candidate SHA is required")
        # Reuse existing Doctor planning gates: trust-boundary modifications,
        # stale comparison base, dirty checkouts and missing Doctor are rejected.
        try:
            advice = DoctorPlanner(self.git, self.interpreter).plan(
                mission_id, candidate_sha)
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
            return ScopedQaResult(candidate_sha, base_sha, "FULL_REQUIRED", (),
                                  advice.report_sha256)
        cmd = [str(self.interpreter), "-B", "-m", "tools.doctor_certification",
               "--base-ref", base_sha, "--expected-sha", candidate_sha,
               "--run"]
        # A child cannot inherit stdin, stdout memory, API keys, or tokens.
        # Parent process must ALSO be run as an isolated unprivileged OS user.
        with tempfile.TemporaryFile() as output:
            try:
                completed = subprocess.run(
                    cmd, cwd=tree.path, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.DEVNULL,
                    timeout=self.timeout_seconds, check=False,
                    env={"PATH": str(self.interpreter.parent),
                         "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1",
                         "GIT_TERMINAL_PROMPT": "0",
                         "NO_PROXY": "127.0.0.1,localhost"})
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ScopedQaError("Scoped Doctor execution failed or timed out") from exc
            output.seek(0)
            raw = output.read(_MAX_REPORT + 1)
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
        passed = (completed.returncode == 0
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
        row = store.db.execute(
            "SELECT candidate_sha FROM missions WHERE id=? AND repo=? "
            "AND state='QA_PENDING'", (mission_id, self.git.repository)).fetchone()
        if row is None or not _SHA.fullmatch(row["candidate_sha"]):
            raise StoreError("Only a QA_PENDING mission with real SHA can be checked")
        result = self.run(mission_id, row["candidate_sha"])
        with store._transaction():
            current = store.db.execute(
                "SELECT candidate_sha FROM missions WHERE id=? AND repo=? "
                "AND state='QA_PENDING'", (mission_id, self.git.repository)).fetchone()
            if current is None or current["candidate_sha"] != result.candidate_sha:
                raise StoreError("QA result became stale during execution")
            store.db.execute("""CREATE TABLE IF NOT EXISTS scoped_qa_advisories (
                mission_id TEXT PRIMARY KEY REFERENCES missions(id),
                candidate_sha TEXT NOT NULL,
                status TEXT NOT NULL,
                base_sha TEXT NOT NULL,
                report_sha256 TEXT NOT NULL,
                checked_epoch INTEGER NOT NULL
            )""")
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

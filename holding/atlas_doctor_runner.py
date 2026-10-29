"""Contained invocation of the repository-owned Dofus Atlas Doctor CLI."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

from .atlas_doctor_evidence import (
    LocalPassport, MAX_REPORT_BYTES, _safe_paths, consume_doctor_report,
)
from .bounded_process import BoundedProcessError, ProcessResult, run_bounded


_SHA = re.compile(r"^[0-9a-f]{40}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_ACTIONS = frozenset({"plan", "check", "validate"})
_LEVELS = frozenset({"SOFT", "MEDIUM", "HARD"})
_ENVIRONMENT = {
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG",
    "LC_ALL", "PYTHONIOENCODING",
}


class DoctorRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorktreeIdentity:
    dirty: bool
    dirty_digest: str


class AtlasDoctorRunner:
    def __init__(self, root: Path, interpreter: Path, git_executable: Path, *,
                 timeout_seconds: int = 180, max_tests: int = 8) -> None:
        if not all(isinstance(path, Path) for path in (
                root, interpreter, git_executable)):
            raise DoctorRunnerError("Invalid trusted Atlas Doctor installation")
        doctor = root / "tools" / "atlas_doctor.py"
        if (not root.is_absolute() or not root.is_dir() or root.is_symlink()
                or not doctor.is_file() or doctor.is_symlink()
                or not (root / "AI_ENTRYPOINT.md").is_file()
                or not interpreter.is_absolute() or not interpreter.is_file()
                or interpreter.is_symlink()
                or not git_executable.is_absolute() or not git_executable.is_file()
                or git_executable.is_symlink()
                or type(timeout_seconds) is not int or not 10 <= timeout_seconds <= 180
                or type(max_tests) is not int or not 1 <= max_tests <= 8):
            raise DoctorRunnerError("Invalid trusted Atlas Doctor installation")
        self.root = root.resolve()
        self.interpreter = interpreter.resolve()
        self.git_executable = git_executable.resolve()
        self.timeout_seconds = timeout_seconds
        self.max_tests = max_tests
        self._verified_diagnostic: tuple[str, str, str, bytes] | None = None

    @staticmethod
    def _environment() -> dict[str, str]:
        result = {
            key: value for key, value in os.environ.items()
            if key.upper() in _ENVIRONMENT
        }
        result.update(
            PYTHONNOUSERSITE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
            GIT_TERMINAL_PROMPT="0", NO_PROXY="127.0.0.1,localhost",
        )
        return result

    def _execute(self, argv: tuple[str, ...], *, timeout: float,
                 stdout_limit: int = MAX_REPORT_BYTES) -> ProcessResult:
        try:
            return run_bounded(
                argv, cwd=self.root, environment=self._environment(),
                timeout_seconds=timeout, stdout_limit=stdout_limit,
                stderr_limit=256 * 1024,
            )
        except (OSError, BoundedProcessError) as exc:
            raise DoctorRunnerError("Contained process execution failed") from exc

    def _git(self, *arguments: str, check: bool = True,
             output_limit: int = MAX_REPORT_BYTES) -> bytes:
        result = self._execute(
            (str(self.git_executable), *arguments), timeout=15,
            stdout_limit=output_limit,
        )
        if check and result.returncode != 0:
            raise DoctorRunnerError("Trusted Git inspection failed")
        return result.stdout

    def _worktree_identity(self) -> WorktreeIdentity:
        status = self._git(
            "status", "--short", "--untracked-files=all",
        ).decode("utf-8", errors="strict").strip()
        if not status:
            return WorktreeIdentity(False, "")
        digest = hashlib.sha256()
        tracked_diff = self._git(
            "diff", "--binary", "HEAD", "--", output_limit=8 * 1024 * 1024,
        ).decode("utf-8", errors="replace").strip()
        digest.update(tracked_diff.encode("utf-8", errors="replace"))
        untracked = self._git(
            "ls-files", "--others", "--exclude-standard", "-z",
        ).decode("utf-8", errors="strict").strip().split("\0")
        total = 0
        for relative in sorted(item for item in untracked if item):
            try:
                safe = _safe_paths([relative])[0]
            except ValueError as exc:
                raise DoctorRunnerError("Unsafe untracked Doctor input") from exc
            digest.update(safe.encode("utf-8"))
            path = self.root / safe
            if path.is_symlink() or not path.is_file():
                raise DoctorRunnerError("Unsafe untracked Doctor input")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > 64 * 1024 * 1024:
                        raise DoctorRunnerError("Untracked Doctor input exceeds digest budget")
                    digest.update(chunk)
        return WorktreeIdentity(True, digest.hexdigest())

    def take_verified_diagnostic(self, *, action: str, candidate_sha: str,
                                 report_sha256: str) -> bytes:
        """One-shot, exact-SHA and digest-bound report; never executes Doctor."""
        saved, self._verified_diagnostic = self._verified_diagnostic, None
        if (saved is None or action not in {"check", "validate"}
                or not isinstance(candidate_sha, str)
                or not _SHA.fullmatch(candidate_sha)
                or not isinstance(report_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", report_sha256)):
            raise DoctorRunnerError("No matching verified Doctor refusal")
        recorded_action, recorded_sha, digest, raw = saved
        if (recorded_action != action or recorded_sha != candidate_sha
                or digest != report_sha256
                or hashlib.sha256(raw).hexdigest() != digest):
            raise DoctorRunnerError("Stale or mismatched Doctor refusal evidence")
        return raw

    def run(self, action: str, paths: tuple[str, ...], *, candidate_sha: str,
            base_ref: str = "HEAD", structural: bool = False,
            level: str | None = None, run_tests: bool = False,
            capture_verified_refusal: bool = False) -> LocalPassport:
        self._verified_diagnostic = None
        if (action not in _ACTIONS or type(paths) is not tuple
                or type(capture_verified_refusal) is not bool
                or not isinstance(candidate_sha, str) or not _SHA.fullmatch(candidate_sha)
                or not isinstance(base_ref, str) or not _REF.fullmatch(base_ref)
                or ".." in base_ref or "//" in base_ref
                or type(structural) is not bool or type(run_tests) is not bool
                or level is not None and level not in _LEVELS
                or run_tests and action != "check"):
            raise DoctorRunnerError("Invalid trusted Doctor request")
        try:
            safe_paths = _safe_paths(list(paths))
        except ValueError as exc:
            raise DoctorRunnerError("Invalid trusted Doctor request") from exc
        if not safe_paths:
            raise DoctorRunnerError("Doctor requires an explicit path scope")
        head = self._git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        if head != candidate_sha:
            raise DoctorRunnerError("Atlas checkout is not at the requested SHA")
        before = self._worktree_identity()
        argv = [
            str(self.interpreter), "-B", "-m", "tools.atlas_doctor", "--json",
            "agent", action, *safe_paths, "--base-ref", base_ref,
            "--max-tests", str(self.max_tests),
            "--timeout-seconds", str(self.timeout_seconds),
        ]
        if structural:
            argv.append("--structural")
        if level is not None:
            argv.extend(("--level", level))
        if run_tests:
            argv.append("--run-tests")
        result = self._execute(tuple(argv), timeout=self.timeout_seconds + 15)
        after_head = self._git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        after = self._worktree_identity()
        if after_head != candidate_sha or after != before:
            raise DoctorRunnerError("Atlas checkout changed during Doctor execution")
        try:
            passport = consume_doctor_report(
                result.stdout, observed_exit_code=result.returncode,
                expected_sha=candidate_sha, expected_action=action,
                expected_base_ref=base_ref, expected_paths=safe_paths,
                expected_dirty=before.dirty,
                expected_dirty_digest=before.dirty_digest,
                structural=structural,
            )
            if (capture_verified_refusal and action in {"check", "validate"}
                    and passport.status in {"REVIEW", "FAIL", "BLOCKED"}):
                self._verified_diagnostic = (
                    action, candidate_sha, passport.report_sha256, result.stdout,
                )
            return passport
        except ValueError as exc:
            raise DoctorRunnerError("Doctor returned invalid evidence") from exc

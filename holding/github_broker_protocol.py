"""Bounded JSON IPC between the Holding writer and a privileged GitHub broker."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from .github_full_evidence import FullEvidence, GitHubEvidenceError
from .github_mutation_gate import GitHubMutationIntent
from .github_pr_inventory import OpenPrInventory, PrInventoryError, validate_open_pr_inventory
from .github_pr_changed_paths import (PrChangedPaths, PrChangedPathsError,
                                     validate_pr_changed_paths)
from .git_push import GitPushState
from .sync_coordinator import LiveGitHubState, MutationFailure


SCHEMA_VERSION = 1
MAX_MESSAGE_BYTES = 64 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ACTIONS = frozenset({
    "observe_account", "observe_pr", "observe_full", "observe_git",
    "observe_open_prs", "observe_pr_paths",
    "mutate", "push_git",
})
_ACCOUNT = re.compile(
    r"^github:[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class GitHubAccountProbeError(RuntimeError):
    def __init__(self, code: str):
        if code not in {"TRANSIENT_UNAVAILABLE", "RATE_LIMITED", "REJECTED"}:
            raise ValueError("Invalid account probe failure")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BrokerFilePin:
    path: Path
    sha256: str


@dataclass(frozen=True)
class BrokerProcessConfig:
    command: tuple[str, ...]
    file_pins: tuple[BrokerFilePin, ...]
    spool_root: Path
    timeout_seconds: int = 30


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_config(config: BrokerProcessConfig) -> None:
    if (not isinstance(config, BrokerProcessConfig)
            or not isinstance(config.command, tuple) or not config.command
            or not all(isinstance(item, str) and item for item in config.command)
            or any(item in {"-c", "-m"} for item in config.command[1:])
            or type(config.timeout_seconds) is not int
            or not 1 <= config.timeout_seconds <= 120
            or not isinstance(config.spool_root, Path)
            or not config.spool_root.is_absolute()
            or not config.spool_root.is_dir()
            or config.spool_root.is_symlink()
            or not config.file_pins):
        raise GitHubEvidenceError("Invalid privileged broker configuration")
    pinned = {}
    for pin in config.file_pins:
        if (not isinstance(pin, BrokerFilePin)
                or not isinstance(pin.path, Path) or not pin.path.is_absolute()
                or not isinstance(pin.sha256, str)
                or not _DIGEST.fullmatch(pin.sha256)):
            raise GitHubEvidenceError("Invalid privileged broker file pin")
        try:
            if pin.path != pin.path.resolve(strict=True):
                raise GitHubEvidenceError(
                    "Broker file pin must use a canonical path")
        except OSError:
            raise GitHubEvidenceError("Broker pinned file is unavailable") from None
        pinned[str(pin.path)] = pin.sha256
    executable = Path(config.command[0])
    if (not executable.is_absolute() or executable != executable.resolve()
            or str(executable) not in pinned):
        raise GitHubEvidenceError("Broker executable must be absolutely pinned")
    for argument in config.command[1:]:
        path = Path(argument)
        if path.suffix.lower() in {".py", ".pyw", ".exe", ".dll"}:
            if (not path.is_absolute() or path != path.resolve()
                    or str(path) not in pinned):
                raise GitHubEvidenceError("Broker code argument must be absolutely pinned")
            if path.suffix.lower() in {".py", ".pyw"}:
                try:
                    siblings = tuple(path.parent.glob("*.py"))
                except OSError:
                    raise GitHubEvidenceError(
                        "Broker source inventory unavailable") from None
                if any(str(source.resolve()) not in pinned for source in siblings):
                    raise GitHubEvidenceError(
                        "Broker Python package must be completely pinned")


def _verify_pins(config: BrokerProcessConfig) -> None:
    for pin in config.file_pins:
        try:
            if (not pin.path.is_file() or pin.path.is_symlink()
                    or _file_digest(pin.path) != pin.sha256):
                raise GitHubEvidenceError("Privileged broker file pin mismatch")
        except OSError:
            raise GitHubEvidenceError("Privileged broker file unavailable") from None


def _minimal_environment() -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    environment = {
        key: os.environ[key] for key in allowed if key in os.environ
    }
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1"})
    return environment


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=6, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            process.kill()
    else:
        process.kill()
    try:
        process.wait(timeout=6)
    except subprocess.TimeoutExpired:
        pass


class GitHubBrokerProcessControl:
    """PrivilegedGitHubControl implementation over a pinned one-shot broker."""

    def __init__(self, config: BrokerProcessConfig):
        _validate_config(config)
        self.config = config

    def _invoke(
        self, action: str, payload: dict[str, Any], *, mutation: bool,
    ) -> dict[str, Any]:
        if action not in _REQUEST_ACTIONS or not isinstance(payload, dict):
            raise GitHubEvidenceError("Invalid privileged broker request")
        try:
            _verify_pins(self.config)
        except GitHubEvidenceError:
            if mutation:
                raise MutationFailure(
                    "REJECTED", retryable=False, outcome_known=True) from None
            raise
        base = {
            "schema_version": SCHEMA_VERSION,
            "action": action,
            "payload": payload,
        }
        request_id = hashlib.sha256(_canonical(base)).hexdigest()
        raw_request = _canonical({**base, "request_id": request_id})
        if len(raw_request) > MAX_MESSAGE_BYTES:
            raise GitHubEvidenceError("Privileged broker request is oversized")
        process = None
        try:
            with tempfile.TemporaryDirectory(
                    prefix="github-broker-", dir=self.config.spool_root) as directory:
                root = Path(directory)
                input_path = root / "request.json"
                output_path = root / "response.json"
                input_path.write_bytes(raw_request)
                with input_path.open("rb") as stdin, output_path.open("wb") as stdout:
                    process = subprocess.Popen(
                        list(self.config.command), stdin=stdin, stdout=stdout,
                        stderr=subprocess.DEVNULL, cwd=root,
                        env=_minimal_environment(), shell=False,
                        creationflags=(
                            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                            if os.name == "nt" else 0),
                    )
                    try:
                        code = process.wait(timeout=self.config.timeout_seconds)
                    except subprocess.TimeoutExpired:
                        _stop_process(process)
                        raise
                size = output_path.stat().st_size
                if code != 0 or not 1 <= size <= MAX_MESSAGE_BYTES:
                    raise ValueError("invalid broker process result")
                raw_response = output_path.read_bytes()
        except (OSError, subprocess.SubprocessError, ValueError):
            if process is not None:
                _stop_process(process)
            if mutation:
                raise MutationFailure(
                    "TRANSIENT_UNAVAILABLE", retryable=True,
                    outcome_known=False,
                ) from None
            raise GitHubEvidenceError("Privileged GitHub broker unavailable") from None
        try:
            response = json.loads(
                raw_response, object_pairs_hook=_unique_object)
        except (UnicodeError, ValueError):
            response = None
        if (not isinstance(response, dict)
                or response.get("schema_version") != SCHEMA_VERSION
                or response.get("request_id") != request_id
                or response.get("status") not in {"OK", "ERROR"}):
            if mutation:
                raise MutationFailure(
                    "TRANSIENT_UNAVAILABLE", retryable=True,
                    outcome_known=False)
            raise GitHubEvidenceError("Invalid privileged broker response")
        if response["status"] == "ERROR":
            if set(response) != {
                    "schema_version", "request_id", "status", "code",
                    "retryable", "outcome_known"} or (
                    type(response["retryable"]) is not bool
                    or type(response["outcome_known"]) is not bool):
                raise GitHubEvidenceError("Invalid privileged broker error")
            if not mutation:
                if action == "observe_account":
                    try:
                        raise GitHubAccountProbeError(response["code"])
                    except (TypeError, ValueError):
                        raise GitHubAccountProbeError(
                            "TRANSIENT_UNAVAILABLE") from None
                raise GitHubEvidenceError("Privileged GitHub observation failed")
            try:
                raise MutationFailure(
                    response["code"], retryable=response["retryable"],
                    outcome_known=response["outcome_known"])
            except (TypeError, ValueError):
                raise MutationFailure(
                    "TRANSIENT_UNAVAILABLE", retryable=True,
                    outcome_known=False) from None
        if set(response) != {"schema_version", "request_id", "status", "result"}:
            if mutation:
                raise MutationFailure(
                    "TRANSIENT_UNAVAILABLE", retryable=True,
                    outcome_known=False)
            raise GitHubEvidenceError("Invalid privileged broker success")
        if not isinstance(response["result"], dict):
            raise GitHubEvidenceError("Invalid privileged broker result")
        return response["result"]

    def observe_pr_paths(self, repository: str, pr_number: int,
                         head_sha: str) -> PrChangedPaths:
        """Expose only revalidated changed paths for one fixed PR SHA."""
        if (not isinstance(head_sha, str) or not re.fullmatch(
                r"[0-9a-f]{40}", head_sha)
                or type(pr_number) is not int or pr_number < 1):
            raise GitHubEvidenceError("Invalid PR path observer identity")
        result = self._invoke("observe_pr_paths", {
            "repository": repository, "pr_number": pr_number,
            "head_sha": head_sha,
        }, mutation=False)
        try:
            report = validate_pr_changed_paths(repository, result)
        except PrChangedPathsError as exc:
            raise GitHubEvidenceError("Invalid privileged PR path evidence") from exc
        if report.pr_number != pr_number or report.head_sha != head_sha:
            raise GitHubEvidenceError("Privileged PR path evidence head mismatch")
        return report

    def observe_open_prs(self, repository: str) -> OpenPrInventory:
        """Read-only, bounded PR hints; deliberately no authority to certify."""
        raw = self._invoke("observe_open_prs", {"repository": repository},
                           mutation=False)
        try:
            return validate_open_pr_inventory(repository, raw)
        except PrInventoryError as exc:
            raise GitHubEvidenceError("Invalid privileged PR inventory") from exc

    def observe_pr(self, repository: str, pr_number: int) -> LiveGitHubState:
        result = self._invoke(
            "observe_pr", {"repository": repository, "pr_number": pr_number},
            mutation=False,
        )
        if (set(result) != {
                "repository", "pr_number", "head_branch", "head_sha", "state",
                "draft", "complete", "merged", "ci_run_id", "ci_head_sha"}
                or not isinstance(result["repository"], str)
                or type(result["pr_number"]) is not int
                or result["pr_number"] < 1
                or not isinstance(result["head_branch"], str)
                or not isinstance(result["head_sha"], str)
                or result["state"] not in {"open", "closed"}
                or type(result["draft"]) is not bool
                or type(result["complete"]) is not bool
                or type(result["merged"]) is not bool
                or (result["ci_run_id"] is not None and (
                    type(result["ci_run_id"]) is not int
                    or result["ci_run_id"] < 1))
                or (result["ci_head_sha"] is not None
                    and not isinstance(result["ci_head_sha"], str))):
            raise GitHubEvidenceError("Invalid privileged PR observation") from None
        return LiveGitHubState(**result)

    def observe_account(self) -> str:
        result = self._invoke("observe_account", {}, mutation=False)
        if (set(result) != {"account_id"}
                or not isinstance(result["account_id"], str)
                or not _ACCOUNT.fullmatch(result["account_id"])):
            raise GitHubEvidenceError("Invalid privileged account observation")
        return result["account_id"]

    def observe_full(self, repository: str, pr_number: int) -> FullEvidence:
        result = self._invoke(
            "observe_full", {"repository": repository, "pr_number": pr_number},
            mutation=False,
        )
        if (set(result) != {
                "repository", "pr_number", "candidate_sha", "run_id", "job_id",
                "status"}
                or not isinstance(result["repository"], str)
                or type(result["pr_number"]) is not int or result["pr_number"] < 1
                or not isinstance(result["candidate_sha"], str)
                or type(result["run_id"]) is not int or result["run_id"] < 1
                or type(result["job_id"]) is not int or result["job_id"] < 1
                or result["status"] != "FULL_VERIFIED_BY_GITHUB"):
            raise GitHubEvidenceError("Invalid privileged FULL observation") from None
        return FullEvidence(**result)

    def mutate(self, intent: GitHubMutationIntent, idempotency_key: str) -> None:
        if not isinstance(intent, GitHubMutationIntent):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=True)
        result = self._invoke(
            "mutate", {
                "intent": asdict(intent), "idempotency_key": idempotency_key,
            }, mutation=True,
        )
        if result:
            raise MutationFailure(
                "TRANSIENT_UNAVAILABLE", retryable=True, outcome_known=False)


class BrokerGitPushControl:
    """Exact-source Git observer/pusher executed by the privileged broker."""

    def __init__(self, broker: GitHubBrokerProcessControl, source_id: str):
        if (not isinstance(broker, GitHubBrokerProcessControl)
                or not isinstance(source_id, str)
                or not _SOURCE_ID.fullmatch(source_id)):
            raise GitHubEvidenceError("Invalid privileged Git source")
        self.broker = broker
        self.source_id = source_id

    def observe(self, repository: str, branch: str,
                candidate_sha: str) -> GitPushState:
        result = self.broker._invoke("observe_git", {
            "source_id": self.source_id, "repository": repository,
            "branch": branch, "candidate_sha": candidate_sha,
        }, mutation=False)
        if (set(result) != {
                "repository", "branch", "candidate_sha", "remote_sha",
                "fast_forward_safe"}
                or not isinstance(result["repository"], str)
                or not isinstance(result["branch"], str)
                or not isinstance(result["candidate_sha"], str)
                or (result["remote_sha"] is not None
                    and not isinstance(result["remote_sha"], str))
                or type(result["fast_forward_safe"]) is not bool):
            raise GitHubEvidenceError("Invalid privileged Git observation")
        return GitPushState(**result)

    def push(self, intent: GitHubMutationIntent,
             idempotency_key: str) -> None:
        if (not isinstance(intent, GitHubMutationIntent)
                or intent.operation != "github.push"):
            raise MutationFailure("REJECTED", retryable=False, outcome_known=True)
        result = self.broker._invoke("push_git", {
            "source_id": self.source_id, "intent": asdict(intent),
            "idempotency_key": idempotency_key,
        }, mutation=True)
        if result:
            raise MutationFailure(
                "TRANSIENT_UNAVAILABLE", retryable=True, outcome_known=False)

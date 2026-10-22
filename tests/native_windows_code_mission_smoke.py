"""End-to-end native Windows coding role: frozen agent -> worktree -> local commit.

Default CI mode uses a disposable local Ollama *stub*, NOT a language model.
Optional --model-json uses an already installed, digest-pinned REAL Ollama
model on the operator's machine. Neither mode runs untrusted code, pushes,
opens a PR, or asserts a Doctor/CI verdict. All Git writes are in a temporary
fixture repository and require test-scope code+commit grants.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

# Native scripts are launched from tests/ rather than as a Python module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from holding.autonomy import Mission, StandingGrant
from holding.ollama_observation import OllamaAllocation, OllamaAllocationObserver
from holding.code_worker import LocalCodeWorker
from holding.daemon_lease import DaemonLease
from holding.executor import AuthorizedGit
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram, LocalProcessAgent
from holding.roadmap_import import RoadmapImporter
from holding.state_store import StateStore, StoreError

REPOSITORY = "r9327/Dof-Atlas"
MODEL = "atlas-native-code-smoke:Q4"
DIGEST = "a" * 64
CRITERIA = [
    "Create src/feature.py with exactly one function named answer "
    "that returns the integer 42. Do not change other files."
]
PATCH = {
    "files": [{"path": "src/feature.py",
               "content": "def answer():\n    return 42\n"}],
    "commit_message": "feat: create answer function for isolated smoke",
}


class LocalOllamaStub(BaseHTTPRequestHandler):
    chat_calls = 0

    def log_message(self, *_):
        pass

    def _reply(self, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/api/tags":
            self.send_error(404)
            return
        self._reply({"models": [{"name": MODEL, "digest": DIGEST}]})

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length") or "0")
        if not 0 < size < 100 * 1024:
            self.send_error(413)
            return
        try:
            req = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            self.send_error(400)
            return
        if (req.get("model") != MODEL or req.get("stream") is not False
                or not isinstance(req.get("format"), dict)
                or not isinstance(req.get("messages"), list)
                or not any(CRITERIA[0] in msg.get("content", "")
                           for msg in req["messages"]
                           if isinstance(msg, dict))):
            self.send_error(400)
            return
        type(self).chat_calls += 1
        self._reply({"message": {"role": "assistant",
                                 "content": json.dumps(PATCH)}})


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], check=True, shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=25,
    )
    return proc.stdout.decode("utf-8", errors="replace").strip()


# Compare the FULL syntax tree, not only the function body. An otherwise
# correct `return 42` may hide side effects in decorators, default values,
# annotations, type parameters, positional-only/keyword-only arguments, or
# global expressions evaluated later on import. Comments/whitespace are fine.
_EXACT_ANSWER_AST = ast.dump(
    ast.parse("def answer():\n    return 42\n"),
    include_attributes=False,
)


def _check_answer(candidate: Path) -> None:
    """Static, NON-EXECUTING checker for exactly one harmless function."""
    if (not isinstance(candidate, Path) or candidate.is_symlink()
            or not candidate.is_file()
            or not 1 <= candidate.stat().st_size <= 8192):
        raise RuntimeError("Unsafe or oversized candidate source")
    try:
        syntax = ast.parse(candidate.read_text(encoding="utf-8"),
                           filename=str(candidate))
    except (SyntaxError, UnicodeError, ValueError) as exc:
        raise RuntimeError("Invalid candidate Python source") from exc
    if ast.dump(syntax, include_attributes=False) != _EXACT_ANSWER_AST:
        raise RuntimeError("Independent static acceptance check failed")

def _mission_receipt(*, live_model: bool, model_name: str,
                     model_digest: str, agent_sha: str, base_sha: str,
                     candidate_sha: str, adapter_peak: int | None,
                     duration_ms: int,
                     allocation: OllamaAllocation | None = None) -> dict:
    """A compact, honest, nonrelease receipt, never a Doctor/release PASS."""
    sha256 = re.compile(r"[0-9a-f]{64}")
    commit = re.compile(r"[0-9a-f]{40}")
    if (type(live_model) is not bool or not isinstance(model_name, str)
            or not 1 <= len(model_name) <= 100
            or not isinstance(model_digest, str) or not sha256.fullmatch(model_digest)
            or not isinstance(agent_sha, str) or not sha256.fullmatch(agent_sha)
            or not isinstance(base_sha, str) or not commit.fullmatch(base_sha)
            or not isinstance(candidate_sha, str) or not commit.fullmatch(candidate_sha)
            or base_sha == candidate_sha
            or type(duration_ms) is not int or not 1 <= duration_ms <= 3_660_000
            or adapter_peak is not None and (
                type(adapter_peak) is not int
                or not 0 <= adapter_peak <= 256 * 1024 ** 2)
            or allocation is not None and (
                not live_model or type(allocation) is not OllamaAllocation
                or type(allocation.observations) is not int
                or not 0 <= allocation.observations <= 1_000_000
                or type(allocation.failed_polls) is not int
                or not 0 <= allocation.failed_polls <= 1_000_000
                or (allocation.observations == 0 and (
                    allocation.loaded_model_bytes_high is not None
                    or allocation.loaded_model_vram_bytes_high is not None))
                or (allocation.observations > 0 and (
                    type(allocation.loaded_model_bytes_high) is not int
                    or type(allocation.loaded_model_vram_bytes_high) is not int
                    or not 0 <= allocation.loaded_model_vram_bytes_high
                    <= allocation.loaded_model_bytes_high <= 1024 ** 5))
            )):
        raise ValueError("Unverified local mission cannot produce a receipt")
    return {
        "schema": 1,
        "kind": "NONRELEASE_DISPOSABLE_LOCAL_MISSION",
        "source": "LIVE_PINNED_OLLAMA" if live_model else "DETERMINISTIC_CI_STUB",
        "target": "ISOLATED_TEST_GIT_REPOSITORY_NOT_REAL_ATLAS",
        "model": model_name,
        "model_digest": model_digest,
        "agent_executable_sha256": agent_sha,
        "base_commit_sha": base_sha,
        "candidate_commit_sha": candidate_sha,
        "changed_paths": ["src/feature.py"],
        "static_ast_exact_match": True,
        "duration_ms": duration_ms,
        "adapter_job_peak_bytes": adapter_peak,
        "ollama_ram_peak_bytes": None,
        "ollama_vram_peak_bytes": None,
        "ollama_allocation_observations": (
            allocation.observations if allocation is not None else 0),
        "ollama_allocation_failed_polls": (
            allocation.failed_polls if allocation is not None else 0),
        "ollama_loaded_model_bytes_observed_high": (
            allocation.loaded_model_bytes_high if allocation is not None else None),
        "ollama_loaded_model_vram_bytes_observed_high": (
            allocation.loaded_model_vram_bytes_high
            if allocation is not None else None),
        "mission_state": "QA_PENDING_AWAITING_PRIVILEGED_DOCTOR",
        "doctor_status": "NOT_EXECUTED",
        "github_push_status": "NOT_PERMITTED",
        "release_ready": False,
    }


def _publish_receipt(target: Path, receipt: dict) -> str:
    """Write only explicit local evidence, never overwrite an older proof."""
    if (not isinstance(target, Path) or not target.is_absolute()
            or target.suffix.lower() != ".json" or target.is_symlink()
            or not target.parent.is_dir() or target.parent.is_symlink()
            or target.parent.resolve(strict=True) != target.parent):
        raise ValueError("Canonical new JSON receipt path required")
    payload = (json.dumps(receipt, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > 4096 or receipt.get("release_ready") is not False:
        raise ValueError("Untrusted or oversized nonrelease mission receipt")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError as exc:
        raise ValueError("Cannot replace an existing mission receipt") from exc
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return hashlib.sha256(payload).hexdigest()


def run(agent: Path, *, real_model: Path | None = None,
        receipt_json: Path | None = None) -> None:
    if os.name != "nt":
        raise RuntimeError("Frozen native Windows coding smoke needs Windows")
    if (not agent.is_absolute() or not agent.is_file() or agent.is_symlink()
            or agent.name != "AtlasHoldingAgent.exe"
            or agent.resolve(strict=True) != agent):
        raise ValueError("Verified native Ollama agent EXE required")
    if shutil.which("git") is None:
        raise RuntimeError("Git is mandatory for isolated worktree proof")
    actual = real_model is not None
    if actual:
        from holding.ollama_protocol import _config, _installed_model
        if (not real_model.is_absolute() or real_model.is_symlink()
                or real_model.resolve(strict=True) != real_model):
            raise ValueError("Real model must use a canonical absolute JSON file")
        model_config = _config(real_model)
        _installed_model(model_config)
    else:
        server = ThreadingHTTPServer(("127.0.0.1", 11434), LocalOllamaStub)
        server_thread = threading.Thread(
            target=server.serve_forever, daemon=True)
        server_thread.start()
    try:
        with tempfile.TemporaryDirectory(
                prefix="atlas-code-role-nonrelease-") as tmp:
            root = Path(tmp).resolve()
            if not actual:
                real_model = root / "pinned-model.json"
                real_model.write_text(json.dumps({
                    "schema": 1, "model": MODEL, "digest": DIGEST,
                    "num_ctx": 4096, "timeout": 12,
                }), encoding="utf-8")
            canonical = root / "canonical"
            canonical.mkdir()
            _git(canonical, "init", "-b", "main")
            _git(canonical, "config", "user.name", "Atlas CI")
            _git(canonical, "config", "user.email", "atlas-smoke@example.invalid")
            _git(canonical, "remote", "add", "origin",
                 "https://github.com/r9327/Dof-Atlas.git")
            (canonical / "README.md").write_text(
                "This is an isolated test repository, not Dof-Atlas.\n",
                encoding="utf-8")
            _git(canonical, "add", "README.md")
            _git(canonical, "commit", "-m", "test: isolated initial commit")
            base = _git(canonical, "rev-parse", "HEAD")
            trees = root / "worktrees"
            trees.mkdir()
            health_dir = root / "health"
            health_dir.mkdir()
            store = StateStore(root / "holding.sqlite")
            try:
                RoadmapImporter(store).import_text(json.dumps({
                    "repo": REPOSITORY,
                    "missions": [{"id": "native-code-smoke",
                                  "operation": "code",
                                  "acceptance": CRITERIA}],
                }), source="explicit-disposable-ci-fixture")
                store.grant_for_onboarding(StandingGrant(
                    REPOSITORY, frozenset({"code", "commit"}),
                    int(time.time()) + 600))
                lease = DaemonLease(store.path, heartbeat=False)
                lease.acquire()
                try:
                    claimed = store.claim_next(
                        online=False, lease_owner=lease.owner)
                    if (claimed.action != "RUN"
                            or claimed.mission_id != "native-code-smoke"):
                        raise RuntimeError("Real persistent roadmap was not claimed")
                    git = GitWorktrees(canonical, trees, REPOSITORY)
                    authorized = AuthorizedGit(store, git, lease)
                    program = AgentProgram(
                        agent, (str(real_model),),
                        # Live model generation may legitimately take many
                        # minutes on the operator's machine. The adapter's
                        # configured bound remains authoritative.
                        timeout_seconds=(
                            model_config["timeout"] + 60 if actual else 35
                        ),
                        pinned_executable_sha256=_sha(agent),
                        pinned_arguments=((str(real_model), _sha(real_model)),),
                    )
                    child = LocalProcessAgent(program)
                    worker = LocalCodeWorker(
                        authorized, child, health_directory=health_dir)
                    if not worker.ready():
                        raise RuntimeError("Native agent/model health unavailable")
                    started = time.monotonic()
                    observer = (OllamaAllocationObserver(model_config["model"])
                                if actual else None)
                    allocation = None
                    try:
                        if observer is not None:
                            observer.start()
                        candidate_sha = worker(Mission(
                            "native-code-smoke", REPOSITORY))
                    finally:
                        if observer is not None:
                            allocation = observer.stop()
                    duration_ms = max(1, int((time.monotonic() - started) * 1000))
                    # The Windows Job Object covers only the lightweight
                    # agent process tree, not Ollama's inference/VRAM.
                    # Do not convert unavailable telemetry into a fake zero.
                    adapter_peak = child.last_job_peak_bytes
                    if adapter_peak is not None:
                        if (type(adapter_peak) is not int
                                or not 0 <= adapter_peak <= 256 * 1024**2):
                            raise RuntimeError("Native agent breached its Job cap")
                        print("Native agent Job peak bytes: " + str(adapter_peak))
                    else:
                        print("Native agent Job peak bytes: NOT_MEASURED")
                    path = trees / "native-code-smoke"
                    if not re.fullmatch(r"[0-9a-f]{40}", candidate_sha):
                        raise RuntimeError("No verifiable candidate commit")
                    if _git(canonical, "rev-parse", "HEAD") != base:
                        raise RuntimeError("Canonical main was modified")
                    if _git(path, "rev-parse", "HEAD") != candidate_sha:
                        raise RuntimeError("Claimed SHA differs from worktree")
                    if _git(path, "diff", "--name-only",
                            base, candidate_sha) != "src/feature.py":
                        raise RuntimeError("Agent changed an unauthorized path")
                    if _git(path, "status", "--porcelain"):
                        raise RuntimeError("Candidate worktree not clean after commit")
                    _check_answer(path / "src" / "feature.py")
                    state = store.missions()[0].state
                    if state != "RUNNING":
                        raise RuntimeError("Coding role must return with RUNNING state")
                    store.checkpoint(
                        "native-code-smoke", candidate_sha, lease_owner=lease.owner)
                    if store.missions()[0].state != "QA_PENDING":
                        raise RuntimeError("Candidate was not durably queued for QA")
                    # Only independently authenticated Doctor/CI evidence can
                    # advance this state, never a string supplied by an LLM.
                    try:
                        store.register_test_verdict(
                            "native-code-smoke", candidate_sha=candidate_sha,
                            evidence_sha="e" * 64, verdict="PASS",
                            runner="untrusted-local-script", artifact_id="fake")
                    except StoreError:
                        pass
                    else:
                        raise RuntimeError("Untrusted local Doctor verdict was accepted")
                    if store.missions()[0].state != "QA_PENDING":
                        raise RuntimeError("Local test improperly promoted the mission")
                    if actual:
                        print("LIVE_MODEL_LOCAL_COMMIT_STATIC_ACCEPTANCE_PASS")
                    else:
                        if LocalOllamaStub.chat_calls != 1:
                            raise RuntimeError("Stub chat count must be exactly one")
                        print("NONRELEASE_FROZEN_AGENT_LOCAL_COMMIT_STUB_PASS")
                    print("Local candidate SHA: " + candidate_sha)
                    if receipt_json is not None:
                        receipt = _mission_receipt(
                            live_model=actual,
                            model_name=(model_config["model"] if actual else MODEL),
                            model_digest=(model_config["digest"] if actual else DIGEST),
                            agent_sha=_sha(agent), base_sha=base,
                            candidate_sha=candidate_sha, adapter_peak=adapter_peak,
                            duration_ms=duration_ms, allocation=allocation,
                        )
                        digest = _publish_receipt(receipt_json, receipt)
                        print("Nonrelease receipt SHA256: " + digest)
                    print("Doctor/CI proof: NOT_EXECUTED")
                    print("GitHub push: NOT_PERMITTED")
                finally:
                    lease.release()
            finally:
                store.close()
    finally:
        if not actual:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, type=Path)
    parser.add_argument(
        "--model-json", type=Path,
        help="Optional installed, pinned real Ollama model: no local stub")
    parser.add_argument(
        "--receipt-json", type=Path,
        help="Write nonrelease local proof in a new JSON file; refuses overwrite")
    args = parser.parse_args()
    run(args.agent, real_model=args.model_json, receipt_json=args.receipt_json)


if __name__ == "__main__":
    main()

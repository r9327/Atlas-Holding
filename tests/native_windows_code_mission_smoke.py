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
from holding.code_worker import LocalCodeWorker
from holding.daemon_lease import DaemonLease
from holding.executor import AuthorizedGit
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram, LocalProcessAgent
from holding.roadmap_import import RoadmapImporter
from holding.state_store import StateStore

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


def _check_answer(candidate: Path) -> None:
    """Independent STATIC criterion; never execute model-generated code."""
    if candidate.stat().st_size > 8192:
        raise RuntimeError("Unbounded candidate source")
    syntax = ast.parse(candidate.read_text(encoding="utf-8"),
                       filename=str(candidate))
    if (len(syntax.body) != 1
            or not isinstance(syntax.body[0], ast.FunctionDef)
            or syntax.body[0].name != "answer"
            or syntax.body[0].args.args
            or len(syntax.body[0].body) != 1
            or not isinstance(syntax.body[0].body[0], ast.Return)
            or not isinstance(syntax.body[0].body[0].value, ast.Constant)
            or syntax.body[0].body[0].value.value != 42
            or type(syntax.body[0].body[0].value.value) is not int):
        raise RuntimeError("Independent static acceptance check failed")


def run(agent: Path, *, real_model: Path | None = None) -> None:
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
                    if claimed is None or claimed.id != "native-code-smoke":
                        raise RuntimeError("Real persistent roadmap was not claimed")
                    git = GitWorktrees(canonical, trees, REPOSITORY)
                    authorized = AuthorizedGit(store, git, lease)
                    program = AgentProgram(
                        agent, (str(real_model),),
                        timeout_seconds=120 if actual else 35,
                        pinned_executable_sha256=_sha(agent),
                        pinned_arguments=((str(real_model), _sha(real_model)),),
                    )
                    child = LocalProcessAgent(program)
                    worker = LocalCodeWorker(
                        authorized, child, health_directory=health_dir)
                    if not worker.ready():
                        raise RuntimeError("Native agent/model health unavailable")
                    candidate_sha = worker(Mission(
                        "native-code-smoke", REPOSITORY))
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
                        raise RuntimeError("Local coding spoofed a QA/Doctor verdict")
                    if actual:
                        print("LIVE_MODEL_LOCAL_COMMIT_STATIC_ACCEPTANCE_PASS")
                    else:
                        if LocalOllamaStub.chat_calls != 1:
                            raise RuntimeError("Stub chat count must be exactly one")
                        print("NONRELEASE_FROZEN_AGENT_LOCAL_COMMIT_STUB_PASS")
                    print("Local candidate SHA: " + candidate_sha)
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
    args = parser.parse_args()
    run(args.agent, real_model=args.model_json)


if __name__ == "__main__":
    main()

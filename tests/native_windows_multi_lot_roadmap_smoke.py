"""Three dependent native-agent lots with signed QA and autonomous repair.

This is a real frozen-agent/local-Git integration smoke on Windows.  The model
server and independent QA authority are deterministic disposable fixtures.  It
never contacts or mutates Dof-Atlas, pushes GitHub, runs canonical Doctor, or
claims release readiness.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from holding.autonomy import StandingGrant
from holding.closure import RoadmapClosure
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram
from holding.proofs import ProofVerifier
from holding.qa_inbox import QaInbox, receipt_name
from holding.roadmap_import import RoadmapImporter
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPOSITORY = "r9327/Dof-Atlas"
MODEL = "atlas-multi-lot-ci:Q4"
MODEL_DIGEST = "9" * 64
QA_KEY = b"Disposable independent QA fixture key; never a product secret"

LOTS = (
    ("lot-1", "Create src/lot_one.py containing exactly VALUE = 1.",
     "src/lot_one.py", "VALUE = 1\n", ()),
    ("lot-2", "Create src/lot_two.py containing exactly VALUE = 2.",
     "src/lot_two.py", "VALUE = 2\n", ("lot-1",)),
    ("lot-3", "Create src/lot_three.py containing exactly VALUE = 3.",
     "src/lot_three.py", "VALUE = 3\n", ("lot-2",)),
)


class MultiLotOllamaStub(BaseHTTPRequestHandler):
    chat_calls = 0
    repair_calls = 0

    def log_message(self, *_args) -> None:
        pass

    def _reply(self, document: dict) -> None:
        body = json.dumps(document).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/api/tags":
            self.send_error(404)
            return
        self._reply({"models": [{"name": MODEL, "digest": MODEL_DIGEST}]})

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length") or "0")
        if not 0 < size <= 100 * 1024:
            self.send_error(413)
            return
        try:
            request = json.loads(self.rfile.read(size))
            messages = request["messages"]
            prompt = "\n".join(
                item.get("content", "") for item in messages
                if isinstance(item, dict) and isinstance(item.get("content"), str)
            )
        except (KeyError, TypeError, ValueError, UnicodeError):
            self.send_error(400)
            return
        if (request.get("model") != MODEL or request.get("stream") is not False
                or not isinstance(request.get("format"), dict)):
            self.send_error(400)
            return
        selected = next((lot for lot in LOTS if lot[1] in prompt), None)
        if selected is None:
            self.send_error(400)
            return
        mission_id, _criterion, path, correct, _dependencies = selected
        repair = "previous_failed_candidate_sha" in prompt
        content = ("VALUE = 999\n" if mission_id == "lot-2" and not repair
                   else correct)
        type(self).chat_calls += 1
        if repair:
            type(self).repair_calls += 1
        self._reply({"message": {"role": "assistant", "content": json.dumps({
            "files": [{"path": path, "content": content}],
            "commit_message": (
                "fix: repair lot two after signed QA"
                if repair else "feat: complete " + mission_id),
        })}})


def _git(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args], check=True, shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=30,
    )
    return process.stdout.decode("utf-8", errors="replace").strip()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _signed_qa(
    evidence: Path, *, mission_id: str, candidate_sha: str, verdict: str,
) -> None:
    artifact = evidence / f"artifact-{mission_id}-{candidate_sha}.json"
    artifact.write_text(json.dumps({
        "fixture": "independent-static-qa",
        "mission_id": mission_id,
        "candidate_sha": candidate_sha,
        "verdict": verdict,
    }, sort_keys=True), encoding="utf-8")
    payload = {
        "schema": 1, "mission_id": mission_id, "sha": candidate_sha,
        # The verifier intentionally accepts only the two production-shaped
        # authorities.  This disposable integration fixture exercises the
        # github_certification branch with a private one-run HMAC key; the
        # outer receipt explicitly refuses any GitHub or release claim.
        "runner": "github_certification", "verdict": verdict,
        "artifact": artifact.name,
        "artifact_sha256": _sha(artifact),
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt = {
        "payload": payload,
        "mac": hmac.new(QA_KEY, encoded, hashlib.sha256).hexdigest(),
    }
    target = evidence / receipt_name(mission_id, candidate_sha)
    with target.open("x", encoding="utf-8") as output:
        json.dump(receipt, output, sort_keys=True)


def _publish_receipt(target: Path, document: dict) -> str:
    if (not target.is_absolute() or target.suffix.lower() != ".json"
            or target.exists() or target.is_symlink()
            or not target.parent.is_dir() or target.parent.is_symlink()
            or target.parent.resolve(strict=True) != target.parent):
        raise ValueError("New canonical multi-lot receipt required")
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
    if (len(payload) > 8192 or document.get("release_ready") is not False
            or document.get("github_push_status") != "NOT_PERMITTED"
            or document.get("canonical_doctor_status") != "NOT_EXECUTED"):
        raise ValueError("Multi-lot receipt made an unsafe claim")
    descriptor = os.open(
        target, os.O_CREAT | os.O_EXCL | os.O_WRONLY
        | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return hashlib.sha256(payload).hexdigest()


def run(agent: Path, *, receipt_json: Path | None = None) -> None:
    if os.name != "nt":
        raise RuntimeError("Native multi-lot smoke requires Windows")
    if (not agent.is_absolute() or not agent.is_file() or agent.is_symlink()
            or agent.name != "AtlasHoldingAgent.exe"
            or agent.resolve(strict=True) != agent):
        raise ValueError("Canonical frozen agent is required")
    MultiLotOllamaStub.chat_calls = 0
    MultiLotOllamaStub.repair_calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 11434), MultiLotOllamaStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-three-lot-roadmap-") as tmp:
            root = Path(tmp).resolve()
            canonical = root / "canonical"
            canonical.mkdir()
            _git(canonical, "init", "-b", "main")
            _git(canonical, "config", "user.name", "Atlas CI")
            _git(canonical, "config", "user.email", "atlas-ci@example.invalid")
            _git(canonical, "remote", "add", "origin",
                 "https://github.com/r9327/Dof-Atlas.git")
            (canonical / "README.md").write_text(
                "Disposable multi-lot fixture; never the real Atlas repo.\n",
                encoding="utf-8")
            _git(canonical, "add", "README.md")
            _git(canonical, "commit", "-m", "test: initial disposable state")
            base_sha = _git(canonical, "rev-parse", "HEAD")
            model = root / "model.json"
            model.write_text(json.dumps({
                "schema": 1, "model": MODEL, "digest": MODEL_DIGEST,
                "num_ctx": 4096, "timeout": 15,
            }), encoding="utf-8")
            evidence = root / "evidence"
            evidence.mkdir()
            health = root / "health"
            health.mkdir()
            worktrees = root / "worktrees"
            worktrees.mkdir()
            database = root / "holding.sqlite"
            roadmap = {
                "repo": REPOSITORY,
                "missions": [{
                    "id": mission_id, "operation": "code",
                    "acceptance": [criterion],
                    "depends_on": list(dependencies),
                } for mission_id, criterion, _path, _content, dependencies in LOTS],
            }
            with StateStore(database) as store:
                RoadmapImporter(store).import_text(
                    json.dumps(roadmap), source="disposable-native-three-lot-fixture")
                store.grant_for_onboarding(StandingGrant(
                    REPOSITORY, frozenset({"code", "commit"}),
                    int(time.time()) + 900))
            git = GitWorktrees(canonical, worktrees, REPOSITORY)
            program = AgentProgram(
                agent, (str(model),), timeout_seconds=45,
                pinned_executable_sha256=_sha(agent),
                pinned_arguments=((str(model), _sha(model)),),
            )
            candidates: dict[str, str] = {}
            with HoldingService.from_local_agent(
                    ServiceSettings(database, idle_seconds=0.05),
                    program=program, git=git, health_directory=health,
                    online=lambda: False) as service:
                service.start()
                inbox = QaInbox(service.engine.store,
                                ProofVerifier(evidence, QA_KEY))
                for mission_id, _criterion, path, correct, _dependencies in LOTS:
                    tick = service.run(max_ticks=1)
                    if tick.action != "QA_PENDING" or tick.mission_id != mission_id:
                        raise RuntimeError("Dependency-ordered roadmap did not reach QA")
                    mission = next(
                        item for item in service.engine.store.missions()
                        if item.id == mission_id)
                    candidate = mission.candidate_sha
                    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
                        raise RuntimeError("Missing exact candidate SHA")
                    worktree = worktrees / mission_id
                    if mission_id == "lot-2":
                        if (worktree / path).read_text(encoding="utf-8") != "VALUE = 999\n":
                            raise RuntimeError("Injected reviewer defect was not present")
                        _signed_qa(evidence, mission_id=mission_id,
                                   candidate_sha=candidate, verdict="FAIL")
                        rejected = inbox.poll()
                        if rejected.failed != 1:
                            raise RuntimeError("Signed QA failure was not consumed")
                        repair = service.run(max_ticks=1)
                        if repair.action != "QA_PENDING" or repair.mission_id != mission_id:
                            raise RuntimeError("Failed lot was not repaired autonomously")
                        mission = next(
                            item for item in service.engine.store.missions()
                            if item.id == mission_id)
                        if mission.candidate_sha == candidate:
                            raise RuntimeError("Repair did not produce a new exact SHA")
                        candidate = mission.candidate_sha
                    if (worktree / path).read_text(encoding="utf-8") != correct:
                        raise RuntimeError("Independent lot acceptance failed")
                    if _git(worktree, "status", "--porcelain"):
                        raise RuntimeError("Native agent left a dirty worktree")
                    _signed_qa(evidence, mission_id=mission_id,
                               candidate_sha=candidate, verdict="PASS")
                    accepted = inbox.poll()
                    if accepted.passed != 1:
                        raise RuntimeError("Signed exact-SHA PASS was not consumed")
                    candidates[mission_id] = candidate
                closure = RoadmapClosure(service.engine.store).inspect()
                if (not closure.ready or closure.reason != "ALL_REQUIREMENTS_SIGNED"
                        or closure.requirements.missions_total != 3
                        or closure.requirements.proven != 3):
                    raise RuntimeError("Three-lot roadmap did not close from signed proof")
            if _git(canonical, "rev-parse", "HEAD") != base_sha:
                raise RuntimeError("Disposable canonical main was modified")
            if MultiLotOllamaStub.chat_calls != 4 or MultiLotOllamaStub.repair_calls != 1:
                raise RuntimeError("Expected three lots plus one repair inference")
            document = {
                "schema": 1,
                "kind": "NONRELEASE_NATIVE_THREE_LOT_SIGNED_QA",
                "target": "DISPOSABLE_LOCAL_GIT_NOT_REAL_ATLAS",
                "candidate_shas": candidates,
                "closure_digest": closure.report_digest,
                "signed_qa_passes": 3,
                "signed_qa_failures": 1,
                "autonomous_repairs": 1,
                "canonical_doctor_status": "NOT_EXECUTED",
                "github_push_status": "NOT_PERMITTED",
                "release_ready": False,
            }
            if receipt_json is not None:
                digest = _publish_receipt(receipt_json, document)
                print("Multi-lot receipt SHA256: " + digest)
            print("NONRELEASE_NATIVE_THREE_LOT_SIGNED_QA_REPAIR_PASS")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--receipt-json", type=Path)
    args = parser.parse_args(argv)
    run(args.agent.resolve(strict=True),
        receipt_json=(args.receipt_json.resolve(strict=False)
                      if args.receipt_json is not None else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

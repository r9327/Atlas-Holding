"""Nonrelease native Windows proof for the real frozen AtlasHoldingAgent.exe.

Tests pinned Ollama protocol-v1 over a local fake HTTP server, without any
model, internet, credentials, repository writes, or external Git process.
This is NOT an autonomous coding-cycle certification.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


_DIGEST = "a" * 64
_MODEL = "atlas-ci-smoke:Q4"
_PLAN = {
    "files": [{"path": "src/generated.py", "content": "answer = 42\n"}],
    "commit_message": "feat: implement safe native adapter smoke",
}


class _OllamaStub(BaseHTTPRequestHandler):
    digest = _DIGEST
    chat_calls = 0

    def log_message(self, *_args):
        pass

    def _respond(self, value: dict) -> None:
        raw = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != "/api/tags":
            self.send_error(404)
            return
        self._respond({"models": [{"name": _MODEL, "digest": type(self).digest}]})

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length") or "0")
        if not 0 < size <= 128 * 1024:
            self.send_error(413)
            return
        try:
            requested = json.loads(self.rfile.read(size))
        except ValueError:
            self.send_error(400)
            return
        if (requested.get("model") != _MODEL
                or requested.get("stream") is not False
                or not isinstance(requested.get("format"), dict)):
            self.send_error(400)
            return
        type(self).chat_calls += 1
        self._respond({"message": {"role": "assistant",
                                   "content": json.dumps(_PLAN)}})


def _invoke(agent: Path, config: Path, action: str, payload: dict) -> subprocess.CompletedProcess:
    request = json.dumps({
        "protocol": 1, "action": action, "payload": payload,
    }).encode("utf-8")
    return subprocess.run(
        [str(agent), str(config)], input=request,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=45, check=False, shell=False,
    )


def run(agent: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Native Windows agent proof requires Windows")
    if (not agent.is_absolute() or agent.is_symlink()
            or not agent.is_file() or agent.name != "AtlasHoldingAgent.exe"):
        raise ValueError("Pinned native adapter EXE required")
    offline = subprocess.run(
        [str(agent), "--self-test"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=45, check=False, shell=False,
    )
    if offline.returncode:
        raise RuntimeError("Frozen adapter offline self-test failed")
    rejected = subprocess.run(
        [str(agent)], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=45, check=False, shell=False,
    )
    if rejected.returncode != 2:
        raise RuntimeError("Frozen adapter accepted an unconfigured startup")

    # Exact Ollama port is intentionally fixed and cannot be supplied by
    # a model/roadmap, environment variable, or external proxy.
    server = ThreadingHTTPServer(("127.0.0.1", 11434), _OllamaStub)
    background = threading.Thread(target=server.serve_forever, daemon=True)
    background.start()
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-frozen-agent-") as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "existing.py").write_text(
                "def answer():\n    return 41\n", encoding="utf-8")
            config = root / "model.json"
            values = {
                "schema": 1, "model": _MODEL, "digest": _DIGEST,
                "num_ctx": 4096, "timeout": 10,
            }
            config.write_text(json.dumps(values), encoding="utf-8")
            _OllamaStub.chat_calls = 0
            _OllamaStub.digest = "b" * 64
            invalid = _invoke(agent, config, "health", {})
            if invalid.returncode == 0 or _OllamaStub.chat_calls:
                raise RuntimeError("Frozen adapter accepted changed model digest")
            _OllamaStub.digest = _DIGEST
            ready = _invoke(agent, config, "health", {})
            if ready.returncode != 0 or json.loads(ready.stdout) != {
                "protocol": 1, "action": "health", "status": "ok",
                "agent_ready": True,
            }:
                raise RuntimeError("Frozen adapter health handshake failed")
            plan = _invoke(agent, config, "plan_patch", {
                "mission_id": "native-smoke",
                "repository": "r9327/Dof-Atlas",
                "acceptance": ["Produce a new generated answer file"],
                "base_sha": "c" * 40,
            })
            if (plan.returncode != 0 or json.loads(plan.stdout) != {
                    "protocol": 1, "action": "plan_patch",
                    "status": "ok", "plan": _PLAN,
                } or _OllamaStub.chat_calls != 1):
                raise RuntimeError("Frozen adapter JSON patch exchange failed")
            if (root / "src" / "generated.py").exists():
                raise RuntimeError("Adapter wrote to target instead of proposing a patch")
    finally:
        server.shutdown()
        server.server_close()
        background.join(timeout=3)
    print("NONRELEASE_FROZEN_AGENT_PROTOCOL_V1_PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=Path, required=True)
    args = parser.parse_args()
    run(args.agent)


if __name__ == "__main__":
    main()

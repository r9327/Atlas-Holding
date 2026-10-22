"""Opt-in REAL Ollama inference on a disposable Windows Actions runner.

Not a benchmark, not a Doctor check, not a release. Never touches actual Atlas.
Only invoked from an explicitly labeled PR; no local user installation.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time

from holding.inventory import Component, scan, _api_json
from holding.ollama_service import _local_api_alive, start_local_ollama
from holding.version_evidence import matches_component_version
from holding.windows_ollama_environment import configure_user_models
from holding.windows_package_manager import install_pinned_winget
from holding.winget_binary import discover_winget_binary
from holding.ollama_native_smoke import PIN

MODEL = "qwen2.5-coder:1.5b"  # Small CPU-only integration fixture, NOT a quality pick.
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA64 = re.compile(r"[0-9a-f]{64}\Z")


def _runner_root() -> Path:
    if (platform.system() != "Windows" or os.name != "nt"
            or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != "r9327/Atlas-Holding"
            or os.environ.get("GITHUB_EVENT_NAME") != "pull_request"
            or not os.environ.get("RUNNER_TEMP")
            or not os.environ.get("GITHUB_RUN_ID", "").isdigit()):
        raise RuntimeError("Disposable GitHub Windows pull-request runner required")
    temp = Path(os.environ["RUNNER_TEMP"])
    if not temp.is_absolute() or temp.is_symlink() or not temp.is_dir():
        raise RuntimeError("Unsafe runner temporary directory")
    target = temp / ("holding-live-inference-" + os.environ["GITHUB_RUN_ID"])
    target.mkdir(exist_ok=False)
    return target.resolve(strict=True)


def _pinned_digest(name: str, tags: dict) -> str:
    models = tags.get("models")
    if not isinstance(models, list):
        raise RuntimeError("Ollama model inventory is invalid")
    found = [m for m in models if isinstance(m, dict) and m.get("name") == name]
    if len(found) != 1 or not isinstance(found[0].get("digest"), str):
        raise RuntimeError("Exactly one selected installed Ollama model is required")
    digest = found[0]["digest"]
    if not _SHA64.fullmatch(digest):
        raise RuntimeError("Ollama model has no valid digest")
    return digest


def _check_live_receipt(receipt: dict, *, model: str, digest: str) -> None:
    """Independent fail-closed interpretation of the frozen mission evidence."""
    if not isinstance(receipt, dict):
        raise RuntimeError("Missing actual Ollama receipt")
    fields = {
        "schema": 1,
        "kind": "NONRELEASE_DISPOSABLE_LOCAL_MISSION",
        "source": "LIVE_PINNED_OLLAMA",
        "target": "ISOLATED_TEST_GIT_REPOSITORY_NOT_REAL_ATLAS",
        "model": model,
        "model_digest": digest,
        "mission_state": "QA_PENDING_AWAITING_PRIVILEGED_DOCTOR",
        "doctor_status": "NOT_EXECUTED",
        "github_push_status": "NOT_PERMITTED",
        "release_ready": False,
        "static_ast_exact_match": True,
        "changed_paths": ["src/feature.py"],
        "ollama_ram_peak_bytes": None,
        "ollama_vram_peak_bytes": None,
    }
    if any(type(receipt.get(k)) is not type(v) or receipt.get(k) != v
           for k, v in fields.items()):
        raise RuntimeError("Live receipt claims unknown authority or wrong model")
    base, head = receipt.get("base_commit_sha"), receipt.get("candidate_commit_sha")
    if (not isinstance(base, str) or not _SHA40.fullmatch(base)
            or not isinstance(head, str) or not _SHA40.fullmatch(head)
            or base == head
            or not isinstance(receipt.get("agent_executable_sha256"), str)
            or not _SHA64.fullmatch(receipt["agent_executable_sha256"])
            or type(receipt.get("duration_ms")) is not int
            or not 0 < receipt["duration_ms"] < 3600000):
        raise RuntimeError("Live local candidate proof lacks exact commit identity")
    for field in ("certified", "release_certified", "doctor_pass", "github_push_pass"):
        if receipt.get(field) is True:
            raise RuntimeError("Untrusted certification claim in live-model proof")


def _restricted_environment(model_dir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
        "LOCALAPPDATA", "APPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "PROGRAMDATA", "HOMEDRIVE", "HOMEPATH",
    }}
    env.update(OLLAMA_HOST="127.0.0.1:11434", OLLAMA_MODELS=str(model_dir),
               NO_PROXY="127.0.0.1,localhost", GIT_TERMINAL_PROMPT="0")
    return env


def run(agent: Path, acceptance: Path) -> None:
    root = _runner_root()
    for binary, name in ((agent, "AtlasHoldingAgent.exe"),
                         (acceptance, "AtlasHoldingAcceptance.exe")):
        if (not binary.is_absolute() or binary.name != name
                or not binary.is_file() or binary.is_symlink()):
            raise RuntimeError("Only exact locally built frozen executables accepted")
    if _local_api_alive():
        raise RuntimeError("Foreign Ollama already owns loopback: no takeover")
    snapshot = scan(root)
    if snapshot.ollama.status != "NOT_FOUND":
        raise RuntimeError("Runner is not clean before local Ollama installation")
    model_dir = root / "models"
    configured = configure_user_models(model_dir, authorized=True)
    if configured.status != "USER_MODELS_CREATED":
        raise RuntimeError("Cannot pin runner model directory: " + configured.status)
    installation = install_pinned_winget(
        PIN, authorized=True, online=True, max_seconds=480,
        model_root=model_dir,
    )
    if installation.state != "INSTALLED_NOT_HEALTHCHECKED":
        raise RuntimeError("Pinned Ollama install failed: " + installation.state)
    binary = discover_winget_binary(PIN)
    if binary.state != "EXACT_BINARY_VERIFIED":
        raise RuntimeError("Ollama binary does not match pinned version")
    launch = None
    if not _local_api_alive():
        candidate = replace(snapshot, ollama=Component(
            "FOUND_BROKEN", path=binary.executable, version=binary.version,
            reason="Verified installed binary; local API not ready"))
        launch = start_local_ollama(
            candidate, install_root=root, model_root=model_dir,
            pins=(PIN,), authorized=True, startup_seconds=45,
        )
    try:
        if not _local_api_alive():
            for _ in range(15):
                time.sleep(1)
                if _local_api_alive():
                    break
        if not _local_api_alive():
            raise RuntimeError("Pinned Ollama local API unavailable after installation")
        version = _api_json("/api/version").get("version")
        if not isinstance(version, str) or not matches_component_version(
                "ollama", PIN.version, version):
            raise RuntimeError("Running Ollama version does not match verified pin")
        print("NONRELEASE_REAL_OLLAMA_NATIVE_API_READY", flush=True)
        # A single public ~986 MB CPU fixture on the disposable runner only.
        # Never silently pull to the user's computer or call a cloud model.
        pulled = subprocess.run(
            [binary.executable, "pull", MODEL],
            env=_restricted_environment(model_dir),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False, timeout=600,
        )
        if pulled.returncode != 0:
            raise RuntimeError("One allowed test model could not be pulled")
        digest = _pinned_digest(MODEL, _api_json("/api/tags"))
        pinned = root / "model.json"
        pinned.write_text(json.dumps({
            "schema": 1, "model": MODEL, "digest": digest,
            "num_ctx": 4096, "timeout": 420,
        }), encoding="utf-8")
        receipt = root / "real-receipt.json"
        stdout, stderr = root / "mission.out", root / "mission.err"
        with stdout.open("wb") as out, stderr.open("wb") as err:
            result = subprocess.run(
                [str(acceptance), "--agent", str(agent),
                 "--model-json", str(pinned), "--receipt-json", str(receipt)],
                env=_restricted_environment(model_dir),
                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                check=False, timeout=540, cwd=root,
            )
        if result.returncode:
            raise RuntimeError(
                "Real model was invoked but did not satisfy isolated code "
                "acceptance (not a passing quality benchmark)")
        if not receipt.is_file() or receipt.stat().st_size > 4096:
            raise RuntimeError("Verified live-model receipt is missing or oversized")
        raw = receipt.read_bytes()
        proof = json.loads(raw)
        _check_live_receipt(proof, model=MODEL, digest=digest)
        print("NONRELEASE_REAL_OLLAMA_FROZEN_CODE_COMMIT_PASS", flush=True)
        print("Model digest: " + digest, flush=True)
        print("Receipt SHA256: " + hashlib.sha256(raw).hexdigest(), flush=True)
        print("Doctor: NOT_EXECUTED; Atlas GitHub push: NOT_PERMITTED", flush=True)
    finally:
        if launch is not None and launch.owned and launch.process is not None:
            child = launch.process
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    args = parser.parse_args()
    run(args.agent, args.acceptance)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

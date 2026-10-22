"""Exercise actual PowerShell+frozen-EXE Windows acceptance kit with STUB Ollama.

This CI test must NEVER claim a live-model run. It verifies that the
downloadable test kit works without a separately installed Python and that
mutating a binary pin prevents execution before any model request.
"""
from __future__ import annotations

import argparse
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

# Direct invocation starts inside tests/, not at the checked-out project root.
# Import only the exact CI checkout; never a similarly named installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.native_windows_code_mission_smoke import (
    LocalOllamaStub, MODEL, DIGEST,
)


class _FaultInjectingOllamaStub(LocalOllamaStub):
    """Deterministic failures exercised by the ACTUAL distributed PowerShell.

    The CI server is fake in every mode. Even a "LIVE" receipt only proves
    the pinned agent/launcher protocol, never genuine model inference.
    """
    mode = "normal"
    inventory_calls = 0
    failed_chat_calls = 0

    def do_GET(self):
        if self.path == "/api/tags" and type(self).mode == "digest_drift":
            type(self).inventory_calls += 1
            digest = (DIGEST if type(self).inventory_calls == 1
                      else "b" * 64)
            self._reply({"models": [{"name": MODEL, "digest": digest}]})
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/chat" and type(self).mode == "http_error":
            type(self).failed_chat_calls += 1
            self.send_error(503)
            return
        super().do_POST()


def run(kit: Path) -> None:
    import os
    if os.name != "nt":
        raise RuntimeError("Only real Windows may certify a PowerShell kit")
    kit = kit.resolve(strict=True)
    required = (
        "AtlasHoldingAgent.exe",
        "AtlasHoldingAcceptance.exe",
        "Run-HoldingLiveAcceptance.ps1",
        "nonrelease-manifest.json",
        "README.md",
    )
    if any(not (kit / name).is_file() for name in required):
        raise RuntimeError("Downloadable native acceptance kit is incomplete")
    ps = shutil.which("pwsh")
    if ps is None:
        raise RuntimeError("Native PowerShell 7 required on CI Windows runner")
    manifest = json.loads(
        (kit / "nonrelease-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("release_ready") is not False:
        raise RuntimeError("Kit falsely advertised a release")
    script = kit / "Run-HoldingLiveAcceptance.ps1"

    def launch(evidence: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-File", str(script),
             "-Model", MODEL, "-NumCtx", "4096", "-TimeoutSeconds", "30",
             "-EvidenceDirectory", str(evidence)],
            shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=150, check=False,
        )

    _FaultInjectingOllamaStub.chat_calls = 0
    _FaultInjectingOllamaStub.failed_chat_calls = 0
    _FaultInjectingOllamaStub.inventory_calls = 0
    _FaultInjectingOllamaStub.mode = "normal"
    service = ThreadingHTTPServer(
        ("127.0.0.1", 11434), _FaultInjectingOllamaStub)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-frozen-live-kit-") as tmp:
            root = Path(tmp).resolve()
            evidence = root / "evidence"
            result = launch(evidence)
            if result.returncode:
                raise RuntimeError(
                    "Real PowerShell/frozen kit failed against stub protocol: " +
                    result.stderr.decode("utf-8", errors="replace")[-800:])
            if _FaultInjectingOllamaStub.chat_calls != 1:
                raise RuntimeError("Kit did not perform exactly one Ollama stub chat")
            proofs = list(evidence.glob("receipt-*.json"))
            if len(proofs) != 1 or list(evidence.glob("model-*.json")):
                raise RuntimeError("Live kit did not seal receipt or clean model pin")
            receipt = json.loads(proofs[0].read_text(encoding="utf-8"))
            if (receipt.get("source") != "LIVE_PINNED_OLLAMA"
                    or receipt.get("model") != MODEL
                    or receipt.get("model_digest") != DIGEST
                    or receipt.get("doctor_status") != "NOT_EXECUTED"
                    or receipt.get("mission_state") !=
                    "QA_PENDING_AWAITING_PRIVILEGED_DOCTOR"
                    or receipt.get("release_ready") is not False):
                raise RuntimeError("PowerShell kit receipt misrepresented mission")
            # This is STILL a deterministic mock despite the CLI selecting
            # the "live" HTTP path. Never label the CI result as real inference.
            source = kit / "nonrelease-manifest.json"
            previous = source.read_bytes()
            altered = json.loads(previous)
            altered["files"]["AtlasHoldingAgent.exe"] = "0" * 64
            try:
                source.write_text(
                    json.dumps(altered, sort_keys=True),
                    encoding="utf-8")
                blocked = launch(root / "must-not-be-created")
                if blocked.returncode == 0:
                    raise RuntimeError("Kit accepted a modified SHA256 pin")
                if _FaultInjectingOllamaStub.chat_calls != 1:
                    raise RuntimeError("Tampered kit reached Ollama")
                if (root / "must-not-be-created").exists():
                    raise RuntimeError("Tampered kit created proof directories")
            finally:
                source.write_bytes(previous)

            # A genuine model can be unavailable mid-generation. Refuse to
            # create a receipt and remove the temporary model pin on failure.
            _FaultInjectingOllamaStub.mode = "http_error"
            failed_dir = root / "ollama-unavailable"
            unavailable = launch(failed_dir)
            if unavailable.returncode == 0:
                raise RuntimeError("Ollama HTTP 503 falsely passed acceptance")
            if _FaultInjectingOllamaStub.failed_chat_calls != 1:
                raise RuntimeError("Model failure did not reach real agent")
            if list(failed_dir.glob("receipt-*.json")):
                raise RuntimeError("Failed model generation emitted a success receipt")
            if list(failed_dir.glob("model-*.json")):
                raise RuntimeError("Failed model generation left its pin on disk")

            # A model tag can change after the launcher selected its digest
            # but before the real native adapter checks /api/tags again.
            _FaultInjectingOllamaStub.mode = "digest_drift"
            _FaultInjectingOllamaStub.inventory_calls = 0
            previous_chats = _FaultInjectingOllamaStub.chat_calls
            drift_dir = root / "model-digest-drift"
            drift = launch(drift_dir)
            if drift.returncode == 0:
                raise RuntimeError("Digest changed in flight but mission passed")
            if _FaultInjectingOllamaStub.inventory_calls < 2:
                raise RuntimeError("Native runner did not independently recheck digest")
            if _FaultInjectingOllamaStub.chat_calls != previous_chats:
                raise RuntimeError("Digest drift reached model generation")
            if list(drift_dir.glob("receipt-*.json")):
                raise RuntimeError("Digest drift produced a false success receipt")
            if list(drift_dir.glob("model-*.json")):
                raise RuntimeError("Digest drift left its temporary model pin")
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=3)
    print("NONRELEASE_POWERSHELL_STUB_TAMPER_HTTP_FAILURE_AND_DIGEST_DRIFT_PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    options = parser.parse_args()
    run(options.kit)


if __name__ == "__main__":
    main()

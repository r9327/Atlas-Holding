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
    fail_after_chat_calls = 0
    side_effect_sentinel = ""
    unsafe_patch_calls = 0
    drift_after_chat_count = 0

    def _reply(self, obj: dict) -> None:
        if self.path == "/api/chat" and type(self).mode == "unsafe_patch":
            # If the independent AST checker ever imports/executes generated
            # source, this decorator creates a witness file OUTSIDE the
            # disposable candidate worktree. Merely parsing must not.
            type(self).unsafe_patch_calls += 1
            sentinel = repr(type(self).side_effect_sentinel)
            generated = (
                "@__import__('pathlib').Path(" + sentinel + ").touch()\n"
                "def answer():\n    return 42\n"
            )
            obj = {"message": {
                "role": "assistant",
                "content": json.dumps({
                    "files": [{"path": "src/feature.py", "content": generated}],
                    "commit_message": "test: deliberately unsafe decorator"}),
            }}
        super()._reply(obj)

    def do_GET(self):
        if self.path == "/api/tags":
            type(self).inventory_calls += 1
            if type(self).mode in ("digest_drift", "drift_after_first_trial"):
                if type(self).mode == "digest_drift":
                    okay = type(self).inventory_calls == 1
                else:
                    okay = type(self).chat_calls < type(self).drift_after_chat_count
                self._reply({"models": [{
                    "name": MODEL, "digest": DIGEST if okay else "b" * 64}]})
                return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/chat" and (
                type(self).mode == "http_error"
                or (type(self).mode == "partial_failure"
                    and type(self).chat_calls >= type(self).fail_after_chat_calls)):
            type(self).failed_chat_calls += 1
            self.send_error(503)
            return
        super().do_POST()


def run(kit: Path, *, shell: str = "pwsh") -> None:
    import os
    if os.name != "nt":
        raise RuntimeError("Only real Windows may certify a PowerShell kit")
    if shell not in ("pwsh", "powershell"):
        raise ValueError("Explicit native shell must be PowerShell 7 or Windows 5.1")
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
    ps = shutil.which("pwsh" if shell == "pwsh" else "powershell.exe")
    if ps is None:
        raise RuntimeError("Selected native Windows PowerShell shell is unavailable")
    if shell == "powershell":
        # This is Windows PowerShell 5.1, not a second test of pwsh 7.
        check = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command",
             "$PSVersionTable.PSVersion.Major"],
            check=False, capture_output=True, timeout=15)
        if check.returncode or check.stdout.strip() != b"5":
            raise RuntimeError("Expected stock Windows PowerShell 5.x")
    manifest = json.loads(
        (kit / "nonrelease-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("release_ready") is not False:
        raise RuntimeError("Kit falsely advertised a release")
    script = kit / "Run-HoldingLiveAcceptance.ps1"

    def launch(evidence: Path, *, trials: int = 1) -> subprocess.CompletedProcess:
        return subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-File", str(script),
             "-Model", MODEL, "-NumCtx", "4096", "-TimeoutSeconds", "30",
             "-Trials", str(trials), "-EvidenceDirectory", str(evidence)],
            shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=150, check=False,
        )

    _FaultInjectingOllamaStub.chat_calls = 0
    _FaultInjectingOllamaStub.failed_chat_calls = 0
    _FaultInjectingOllamaStub.inventory_calls = 0
    _FaultInjectingOllamaStub.unsafe_patch_calls = 0
    _FaultInjectingOllamaStub.mode = "normal"
    service = ThreadingHTTPServer(
        ("127.0.0.1", 11434), _FaultInjectingOllamaStub)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="atlas-frozen-live-kit-") as tmp:
            root = Path(tmp).resolve()
            evidence = root / "evidence"
            result = launch(evidence, trials=3)
            if result.returncode:
                raise RuntimeError(
                    "Real PowerShell/frozen kit failed against stub protocol: " +
                    result.stderr.decode("utf-8", errors="replace")[-800:])
            if _FaultInjectingOllamaStub.chat_calls != 3:
                raise RuntimeError("Three independent mock coding trials were not executed")
            proofs = sorted(evidence.glob("receipt-*.json"))
            sessions = list(evidence.glob("session-*.json"))
            if len(proofs) != 3 or len(sessions) != 1 or list(evidence.glob("model-*.json")):
                raise RuntimeError("Native kit did not seal three receipts plus one session")
            import hashlib
            session = json.loads(sessions[0].read_text(encoding="utf-8"))
            if (session.get("kind") != "NONRELEASE_REPEATED_LOCAL_ACCEPTANCE"
                    or session.get("trials_requested") != 3
                    or session.get("trials_passed") != 3
                    or session.get("release_ready") is not False
                    or session.get("model_quality_benchmark") != "NOT_CERTIFIED"
                    or session.get("independent_doctor_qa") != "NOT_EXECUTED"
                    or len(session.get("results", [])) != 3):
                raise RuntimeError("Multiple trials falsely claimed quality certification")
            receipt_digests = {
                hashlib.sha256(path.read_bytes()).hexdigest() for path in proofs}
            if {entry["receipt_sha256"] for entry in session["results"]} != receipt_digests:
                raise RuntimeError("Session digest does not cover all three receipts")
            for path in proofs:
                receipt = json.loads(path.read_text(encoding="utf-8"))
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
            expected_files = {
                "AtlasHoldingAgent.exe",
                "AtlasHoldingAcceptance.exe",
                "Run-HoldingLiveAcceptance.ps1",
                "README.md",
            }
            if set(manifest.get("files", {})) != expected_files:
                raise RuntimeError("Candidate archive is not fully pinned")
            for i, name in enumerate(sorted(expected_files)):
                altered = json.loads(previous)
                altered["files"][name] = "0" * 64
                count_before = _FaultInjectingOllamaStub.inventory_calls
                chats_before = _FaultInjectingOllamaStub.chat_calls
                proofdir = root / f"tampered-pin-{i}"
                try:
                    source.write_text(
                        json.dumps(altered, sort_keys=True), encoding="utf-8")
                    blocked = launch(proofdir)
                    if blocked.returncode == 0:
                        raise RuntimeError("Kit accepted a changed " + name + " pin")
                    if (_FaultInjectingOllamaStub.chat_calls != chats_before or
                            _FaultInjectingOllamaStub.inventory_calls != count_before):
                        raise RuntimeError("Corrupt archive contacted Ollama")
                    if proofdir.exists():
                        raise RuntimeError("Corrupt archive created a proof folder")
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
            if list(failed_dir.glob("session-*.json")):
                raise RuntimeError("Failed model emitted a false session PASS")
            if list(failed_dir.glob("receipt-*.json")):
                raise RuntimeError("Failed model generation emitted a success receipt")
            if list(failed_dir.glob("model-*.json")):
                raise RuntimeError("Failed model generation left its pin on disk")

            # One passing trial followed by a failing second trial must never
            # produce a passing aggregate; retain only the individual receipt.
            _FaultInjectingOllamaStub.mode = "partial_failure"
            _FaultInjectingOllamaStub.fail_after_chat_calls = (
                _FaultInjectingOllamaStub.chat_calls + 1)
            previous_chats = _FaultInjectingOllamaStub.chat_calls
            failed_before = _FaultInjectingOllamaStub.failed_chat_calls
            partial_dir = root / "partially-successful-mission"
            partial = launch(partial_dir, trials=3)
            if partial.returncode == 0:
                raise RuntimeError("Partially failing model was certified as 3/3")
            if (_FaultInjectingOllamaStub.chat_calls != previous_chats + 1
                    or _FaultInjectingOllamaStub.failed_chat_calls != failed_before + 1):
                raise RuntimeError("No proof of success-one-then-fail-second path")
            if len(list(partial_dir.glob("receipt-*.json"))) != 1:
                raise RuntimeError("Partial failure did not retain exactly one trial receipt")
            if list(partial_dir.glob("session-*.json")):
                raise RuntimeError("Partial run emitted a fake aggregate PASS")
            if list(partial_dir.glob("model-*.json")):
                raise RuntimeError("Partial run left a temporary model pin")

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
            if list(drift_dir.glob("receipt-*.json")) or list(drift_dir.glob("session-*.json")):
                raise RuntimeError("Digest drift produced a false success receipt or session")
            if list(drift_dir.glob("model-*.json")):
                raise RuntimeError("Digest drift left its temporary model pin")
            # The model can silently be replaced AFTER trial 1 passed.
            # Every new trial must check its digest independently, and the
            # preceding isolated receipt must never create a session PASS.
            _FaultInjectingOllamaStub.mode = "drift_after_first_trial"
            before_chat = _FaultInjectingOllamaStub.chat_calls
            _FaultInjectingOllamaStub.drift_after_chat_count = before_chat + 1
            replaced_dir = root / "model-replaced-after-first-trial"
            replaced = launch(replaced_dir, trials=3)
            if replaced.returncode == 0:
                raise RuntimeError("Changed Ollama model was accepted in later trial")
            if _FaultInjectingOllamaStub.chat_calls != before_chat + 1:
                raise RuntimeError("Changed model reached another generation")
            if len(list(replaced_dir.glob("receipt-*.json"))) != 1:
                raise RuntimeError("No exact first-trial evidence before replacement")
            if (list(replaced_dir.glob("session-*.json"))
                    or list(replaced_dir.glob("model-*.json"))):
                raise RuntimeError("Changed model left session PASS or pin")

            # Adversarial actual frozen-agent path: the generated code
            # contains an import-time decorator which could write to a file
            # if any checker incorrectly imports or executes that source.
            # The acceptance harness must REJECT it via static AST only.
            _FaultInjectingOllamaStub.mode = "unsafe_patch"
            marker_file = root / "must-never-execute-candidate.txt"
            _FaultInjectingOllamaStub.side_effect_sentinel = str(marker_file)
            previously = _FaultInjectingOllamaStub.chat_calls
            unsafe_dir = root / "malicious-decorator"
            malicious = launch(unsafe_dir)
            if malicious.returncode == 0:
                raise RuntimeError("Import-time decorator passed static QA")
            if (_FaultInjectingOllamaStub.chat_calls != previously + 1
                    or _FaultInjectingOllamaStub.unsafe_patch_calls != 1):
                raise RuntimeError("Malicious patch was not delivered to native agent")
            if marker_file.exists():
                raise RuntimeError("Model-generated source WAS EXECUTED during QA")
            if (list(unsafe_dir.glob("receipt-*.json"))
                    or list(unsafe_dir.glob("session-*.json"))):
                raise RuntimeError("Unsafe generated source created a fake PASS")
            if list(unsafe_dir.glob("model-*.json")):
                raise RuntimeError("Unsafe generated source left temporary pin")
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=3)
    print("NONRELEASE_POWERSHELL_STUB_TAMPER_HTTP_FAILURE_AND_DIGEST_DRIFT_PASS " + shell)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--shell", choices=("pwsh", "powershell"), default="pwsh")
    options = parser.parse_args()
    run(options.kit, shell=options.shell)


if __name__ == "__main__":
    main()

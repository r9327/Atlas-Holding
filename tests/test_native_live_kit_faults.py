"""Portable contract tests for the Windows-only live-kit fault injector.

These tests do not invoke PowerShell, frozen binaries, or a real Ollama model.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import ProxyHandler, build_opener

from tests.native_windows_code_mission_smoke import DIGEST, MODEL
from tests.native_windows_live_kit_smoke import _FaultInjectingOllamaStub


class NativeLiveKitFaultTests(unittest.TestCase):
    def test_model_drift_starts_only_after_first_trial_receipt(self) -> None:
        stub = _FaultInjectingOllamaStub
        saved = (stub.mode, stub.chat_calls, stub.inventory_calls,
                 stub.drift_after_receipt_directory)
        with tempfile.TemporaryDirectory(prefix="atlas-receipt-drift-") as tmp:
            evidence = Path(tmp)
            stub.mode = "drift_after_first_trial"
            stub.inventory_calls = 0
            stub.chat_calls = 0
            stub.drift_after_receipt_directory = evidence
            server = ThreadingHTTPServer(("127.0.0.1", 0), stub)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                opener = build_opener(ProxyHandler({}))
                url = f"http://127.0.0.1:{server.server_port}/api/tags"

                def observed_digest() -> str:
                    with opener.open(url, timeout=3) as response:
                        models = json.load(response)["models"]
                    self.assertEqual(models[0]["name"], MODEL)
                    return models[0]["digest"]

                self.assertEqual(observed_digest(), DIGEST)
                # A chat request alone is not completion or acceptance.
                stub.chat_calls = 1
                self.assertEqual(observed_digest(), DIGEST)
                (evidence / "receipt-trial-one.json").write_text(
                    '{"source":"LIVE_PINNED_OLLAMA"}', encoding="utf-8")
                self.assertEqual(observed_digest(), "b" * 64)
                self.assertEqual(observed_digest(), "b" * 64)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                (stub.mode, stub.chat_calls, stub.inventory_calls,
                 stub.drift_after_receipt_directory) = saved


if __name__ == "__main__":
    unittest.main()

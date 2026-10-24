"""Model pulls must be consented, resumable and SHA-verified by Ollama."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from holding.ollama_model_pull import (ModelPullError, PinnedModel,
                                       pull_pinned_model)

PIN = PinnedModel("quality/model:Q4", "sha256:" + "a" * 64)


class FakeResponse(io.BytesIO):
    status = 200
    def __init__(self, events):
        raw = b"".join(
            (json.dumps(event).encode() + b"\n")
            if not isinstance(event, bytes) else event
            for event in events
        )
        super().__init__(raw)


class FakeOpener:
    def __init__(self, events):
        self.response = FakeResponse(events)
        self.requests = []

    def open(self, req, *, timeout):
        self.requests.append((req, timeout))
        return self.response


class OllamaPullTests(unittest.TestCase):
    def test_existing_exact_digest_no_download_even_offline(self):
        with patch("holding.ollama_model_pull._lookup_digest",
                   return_value=PIN.digest):
            with patch("holding.ollama_model_pull.request.build_opener") as api:
                result = pull_pinned_model(
                    PIN, authorized=False, online=False,
                )
        self.assertEqual(result.state, "ALREADY_VERIFIED")
        self.assertEqual(result.verified_digest, PIN.digest)
        api.assert_not_called()

    def test_existing_other_digest_is_never_replaced(self):
        with patch("holding.ollama_model_pull._lookup_digest",
                   return_value="sha256:" + "b" * 64):
            with patch("holding.ollama_model_pull.request.build_opener") as api:
                result = pull_pinned_model(
                    PIN, authorized=True, online=True,
                )
        self.assertEqual(result.state, "DIFFERENT_LOCAL_DIGEST")
        api.assert_not_called()

    def test_missing_authorization_and_offline_refuse_pull(self):
        for authorized, online, state in (
            (False, True, "AUTH_REQUIRED"),
            (True, False, "NETWORK_REQUIRED"),
        ):
            with self.subTest(authorized=authorized, online=online):
                with patch("holding.ollama_model_pull._lookup_digest",
                           return_value=None):
                    with patch("holding.ollama_model_pull.request.build_opener") as api:
                        result = pull_pinned_model(
                            PIN, authorized=authorized, online=online,
                        )
                self.assertEqual(result.state, state)
                api.assert_not_called()

    def test_streamed_pull_sends_fixed_loopback_request_and_checks_digest(self):
        fake = FakeOpener([
            {"status": "pulling manifest"},
            {"status": "pulling abc", "total": 100, "completed": 42},
            {"status": "success"},
        ])
        values = []
        with patch("holding.ollama_model_pull._lookup_digest",
                   side_effect=[None, PIN.digest]):
            with patch("holding.ollama_model_pull.request.build_opener",
                       return_value=fake):
                result = pull_pinned_model(
                    PIN, authorized=True, online=True,
                    progress=lambda n, total: values.append((n, total)),
                )
        self.assertEqual(result.state, "MODEL_VERIFIED_NOT_BENCHMARKED")
        self.assertEqual(result.events, 3)
        self.assertEqual(values, [(42, 100)])
        req, timeout = fake.requests[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/pull")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(timeout, 15)
        self.assertEqual(json.loads(req.data)["model"], PIN.name)
        self.assertFalse(json.loads(req.data)["insecure"])

    def test_success_without_matching_local_digest_is_not_verified(self):
        fake = FakeOpener([{"status": "success"}])
        with patch("holding.ollama_model_pull._lookup_digest",
                   side_effect=[None, "sha256:" + "b" * 64]):
            with patch("holding.ollama_model_pull.request.build_opener",
                       return_value=fake):
                result = pull_pinned_model(PIN, authorized=True, online=True)
        self.assertEqual(result.state, "DIGEST_MISMATCH")

    def test_cancellation_before_open_does_not_send_request(self):
        with patch("holding.ollama_model_pull._lookup_digest",
                   return_value=None):
            with patch("holding.ollama_model_pull.request.build_opener") as api:
                result = pull_pinned_model(
                    PIN, authorized=True, online=True,
                    cancelled=lambda: True,
                )
        self.assertEqual(result.state, "CANCELLED")
        api.assert_not_called()

    def test_interrupted_response_retains_retryable_status(self):
        fake = FakeOpener([{"status": "pulling manifest"}])
        with patch("holding.ollama_model_pull._lookup_digest",
                   return_value=None):
            with patch("holding.ollama_model_pull.request.build_opener",
                       return_value=fake):
                result = pull_pinned_model(PIN, authorized=True, online=True)
        self.assertEqual(result.state, "INTERRUPTED_RETRYABLE")

    def test_oversized_stream_event_is_rejected(self):
        fake = FakeOpener([b'{"status":"' + b"x" * 20000 + b'"}\n'])
        with patch("holding.ollama_model_pull._lookup_digest",
                   return_value=None):
            with patch("holding.ollama_model_pull.request.build_opener",
                       return_value=fake):
                with self.assertRaisesRegex(ModelPullError, "Oversized"):
                    pull_pinned_model(PIN, authorized=True, online=True)

    def test_invalid_model_name_and_digest_rejected(self):
        for name in ("../private", "http://evil/model", "my model", ".."):
            with self.subTest(name=name):
                with self.assertRaises(ModelPullError):
                    PinnedModel(name, PIN.digest).validate()
        with self.assertRaises(ModelPullError):
            PinnedModel("normal:tag", "not-digest").validate()


if __name__ == "__main__":
    unittest.main()

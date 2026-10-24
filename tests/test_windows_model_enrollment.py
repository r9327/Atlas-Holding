"""Offline tests for installed Ollama digest-to-config enrollment.

No real Ollama service, internet, model download or native ACL assertion.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from holding.windows_model_enrollment import (
    LocalModelEnrollmentError, discover_local_models, enroll_local_model,
)

MODEL = "qwen-coder:Q4"
DIGEST = "a" * 64


class OllamaEnrollmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "agents").mkdir()

    def response(self, content: object, *, status=200):
        body = json.dumps(content).encode("utf-8")
        stream = MagicMock()
        stream.status = status
        stream.headers = {"Content-Length": str(len(body))}
        stream.read.return_value = body
        context = MagicMock()
        context.__enter__.return_value = stream
        client = MagicMock()
        client.open.return_value = context
        return patch("holding.windows_model_enrollment.build_opener",
                     return_value=client)

    def enroll(self, *, model=MODEL, approved=True):
        with patch("holding.windows_model_enrollment.discover_local_models",
                   return_value=((MODEL, DIGEST),)), patch(
                "holding.windows_model_enrollment.validate_runtime_acl"
        ), patch("holding.windows_model_enrollment.secure_runtime_acl"):
            return enroll_local_model(
                self.root, selected_model=model, approved=approved)

    def test_local_tags_are_parsed_without_external_model_authority(self):
        with self.response({"models": [
            {"name": MODEL, "digest": DIGEST},
            {"name": "another:latest", "digest": "b" * 64},
        ]}):
            result = discover_local_models()
        self.assertEqual(result, (("another:latest", "b" * 64), (MODEL, DIGEST)))

    def test_invalid_models_and_digest_conflict_fail_closed(self):
        for payload in (
            {"models": [{"name": MODEL, "digest": "invalid"}]},
            {"models": [{"name": "../hidden", "digest": DIGEST}]},
            {"models": [{"name": MODEL, "digest": DIGEST},
                        {"name": MODEL, "digest": "b" * 64}]},
            {"models": "invalid"},
        ):
            with self.subTest(payload=payload), self.response(payload):
                with self.assertRaises(LocalModelEnrollmentError):
                    discover_local_models()

    def test_verified_model_config_is_deterministic_and_idempotent(self):
        first = self.enroll()
        self.assertEqual(first.status, "MODEL_PINNED_HEALTH_PENDING")
        data = json.loads(first.path.read_bytes())
        self.assertEqual(data, {
            "schema": 1, "model": MODEL, "digest": DIGEST,
            "num_ctx": 8192, "timeout": 600,
        })
        self.assertEqual(first.sha256, hashlib.sha256(first.path.read_bytes()).hexdigest())
        second = self.enroll()
        self.assertEqual(second.status, "REUSED_MODEL_PIN_HEALTH_PENDING")
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(len(list((self.root / "agents").iterdir())), 1)

    def test_missing_approval_model_or_modified_config_never_qualify(self):
        with self.assertRaisesRegex(LocalModelEnrollmentError, "approval"):
            self.enroll(approved=False)
        with self.assertRaisesRegex(LocalModelEnrollmentError, "not present"):
            self.enroll(model="missing:latest")
        config = self.enroll().path
        config.write_text('{"tampered":true}', encoding="utf-8")
        with self.assertRaisesRegex(LocalModelEnrollmentError, "explicit repair"):
            self.enroll()
        self.assertEqual(config.read_text(), '{"tampered":true}')

    def test_native_acl_check_is_mandatory_before_config_creation(self):
        with patch("holding.windows_model_enrollment.discover_local_models",
                   return_value=((MODEL, DIGEST),)), patch(
                "holding.windows_model_enrollment.validate_runtime_acl",
                side_effect=ValueError("untrusted")) as rights, patch(
                "holding.windows_model_enrollment.secure_runtime_acl") as secure:
            with self.assertRaisesRegex(ValueError, "untrusted"):
                enroll_local_model(self.root, selected_model=MODEL, approved=True)
        rights.assert_called_once_with(self.root)
        secure.assert_not_called()
        self.assertFalse((self.root / "agents" / "AtlasHoldingModel.json").exists())


if __name__ == "__main__":
    unittest.main()

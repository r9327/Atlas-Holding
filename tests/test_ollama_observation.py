"""Strict telemetry semantics: loaded-model allocation != RAM/VRAM peak."""
from __future__ import annotations

import io
from unittest.mock import patch
import unittest

from holding import ollama_observation as mod
from holding.ollama_observation import OllamaAllocationObserver


class _Reply:
    status = 200
    headers = {"Content-Length": "100"}

    def __init__(self, json_content):
        self.stream = io.BytesIO(json_content)

    def read(self, size):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


PIN = "a" * 64


class OllamaAllocationTests(unittest.TestCase):
    def get(self, payload, name="sample/model:Q4"):
        import json
        class _Opener:
            def open(self, request, timeout):
                if (request.full_url != "http://127.0.0.1:11434/api/ps"
                        or request.get_method() != "GET" or timeout != 1):
                    raise AssertionError("Non-loopback or unbounded observation")
                return _Reply(json.dumps(payload).encode("utf-8"))
        with patch.object(mod, "build_opener", return_value=_Opener()):
            return mod._read_active_model(name, PIN)

    def test_only_named_active_model_allocation_is_observed(self):
        self.assertEqual(self.get({"models": [
            {"name": "other:Q4", "size": 20_000, "size_vram": 10_000},
            {"name": "sample/model:Q4", "digest": PIN, "size": 12_000, "size_vram": 8_000},
        ]}), (12_000, 8_000))
        self.assertIsNone(self.get({"models": []}))

    def test_untrusted_negative_boolean_or_impossible_gpu_bytes_refused(self):
        for bad in (
            {"size": -1, "size_vram": 0},
            {"size": 1000, "size_vram": 1001},
            {"size": True, "size_vram": 0},
            {"size": "123", "size_vram": 0},
            {"size": 1024 ** 6, "size_vram": 1},
            {"size": 100, "size_vram": -1},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.get({"models": [{"name": "sample/model:Q4", "digest": PIN, **bad}]})

    def test_same_name_with_different_or_absent_digest_is_never_attributed(self):
        for digest in ("b" * 64, None, "BAD"):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(ValueError, "digest changed"):
                    self.get({"models": [{"name": "sample/model:Q4",
                                          "digest": digest, "size": 1024,
                                          "size_vram": 512}]})

    def test_malformed_inventory_refused(self):
        for malformed in ({}, {"models": {}}, {"models": [None]}):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    self.get(malformed)

    def test_bounded_samples_keep_real_high_watermark_and_failures(self):
        obs = OllamaAllocationObserver("sample/model:Q4", PIN)
        outcomes = iter([(5000, 3000), OSError("temporarily busy"),
                         None, (7000, 4000), (6000, 3500)])
        def fake(name, digest):
            self.assertEqual((name, digest), ("sample/model:Q4", PIN))
            item = next(outcomes)
            if isinstance(item, BaseException):
                raise item
            return item
        with patch.object(mod, "_read_active_model", side_effect=fake):
            for _ in range(5):
                obs._poll()
        result = obs.stop()
        self.assertEqual(result.observations, 3)
        self.assertEqual(result.failed_polls, 1)
        self.assertEqual(result.loaded_model_bytes_high, 7000)
        self.assertEqual(result.loaded_model_vram_bytes_high, 4000)

    def test_no_loaded_model_must_remain_unmeasured_not_zero(self):
        obs = OllamaAllocationObserver("tiny:Q4", PIN)
        with patch.object(mod, "_read_active_model", return_value=None):
            obs._poll()
        result = obs.stop()
        self.assertIsNone(result.loaded_model_bytes_high)
        self.assertIsNone(result.loaded_model_vram_bytes_high)
        self.assertEqual(result.observations, 0)
        self.assertEqual(result.failed_polls, 0)

    def test_network_error_is_just_missing_telemetry_not_success(self):
        obs = OllamaAllocationObserver("tiny:Q4", PIN)
        with patch.object(mod, "_read_active_model", side_effect=OSError("down")):
            obs._poll()
        result = obs.stop()
        self.assertEqual(result.failed_polls, 1)
        self.assertIsNone(result.loaded_model_vram_bytes_high)

    def test_invalid_model_identity_is_refused_before_network(self):
        for name in ("", "../token", "bad?x", "a\nb", "x//y", "x" * 101):
            with self.subTest(name=name), self.assertRaises(ValueError):
                OllamaAllocationObserver(name, PIN)
        OllamaAllocationObserver("owner/code:Q4", PIN)
        for bad_digest in ("f", "", "Z" * 64, True, None):
            with self.subTest(digest=bad_digest), self.assertRaises(ValueError):
                OllamaAllocationObserver("owner/code:Q4", bad_digest)

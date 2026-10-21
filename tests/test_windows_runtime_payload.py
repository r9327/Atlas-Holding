"""Staging of the actual bundled frozen binaries; security is still pending."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from holding.windows_runtime_payload import (
    RuntimePayloadError, install_pinned_runtime_pair, verify_runtime_payload,
    install_pinned_adapter, verify_pinned_adapter,
)


FILES = ("AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe")


class EmbeddedRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.source = root / "from-setup"
        self.install_root = root / "user-chosen-disk"
        self.source.mkdir()
        self.install_root.mkdir()
        for i, name in enumerate(FILES):
            (self.source / name).write_bytes(bytes([66+i]) * (1_000_071+i))
        self.pins = {
            name: hashlib.sha256((self.source / name).read_bytes()).hexdigest()
            for name in FILES
        }

    def test_pinned_adapter_is_staged_reused_and_never_overwritten(self):
        adapter = self.source / "AtlasHoldingAgent.exe"
        adapter.write_bytes(b"trusted fixture adapter")
        pin = hashlib.sha256(adapter.read_bytes()).hexdigest()
        self.assertEqual(verify_pinned_adapter(adapter, pin), pin)
        staged = install_pinned_adapter(
            adapter, self.install_root, pin, approved=True)
        self.assertEqual(staged, "STAGED_PINNED_ADAPTER_HEALTH_PENDING")
        target = self.install_root / "agents" / "AtlasHoldingAgent.exe"
        self.assertEqual(target.read_bytes(), adapter.read_bytes())
        reused = install_pinned_adapter(
            adapter, self.install_root, pin, approved=True)
        self.assertEqual(reused, "REUSED_PINNED_ADAPTER_HEALTH_PENDING")
        target.write_bytes(b"modified by another process")
        with self.assertRaises(RuntimePayloadError):
            install_pinned_adapter(adapter, self.install_root, pin, approved=True)
        self.assertEqual(target.read_bytes(), b"modified by another process")

    def test_adapter_refuses_incomplete_pin_and_never_creates_folder(self):
        adapter = self.source / "AtlasHoldingAgent.exe"
        adapter.write_bytes(b"trusted fixture adapter")
        pin = hashlib.sha256(adapter.read_bytes()).hexdigest()
        with self.assertRaises(RuntimePayloadError):
            install_pinned_adapter(adapter, self.install_root, pin, approved=False)
        with self.assertRaises(RuntimePayloadError):
            install_pinned_adapter(adapter, self.install_root, "0" * 64,
                                   approved=True)
        with self.assertRaises(RuntimePayloadError):
            verify_pinned_adapter(self.source / "foreign.exe", pin)
        self.assertFalse((self.install_root / "agents").exists())

    def test_two_native_binaries_staged_and_reused_without_overwrite(self):
        self.assertEqual(verify_runtime_payload(self.source, self.pins),
                         self.pins)
        first = install_pinned_runtime_pair(
            self.source, self.install_root, self.pins, approved=True)
        self.assertEqual(first.state, "STAGED_BINARIES_SECURITY_PENDING")
        self.assertIs(first.ready, False)
        self.assertEqual(verify_runtime_payload(first.directory, self.pins),
                         self.pins)
        second = install_pinned_runtime_pair(
            self.source, self.install_root, self.pins, approved=True)
        self.assertEqual(second.state, "REUSED_BINARIES_SECURITY_PENDING")
        self.assertEqual(first.directory, second.directory)
        self.assertFalse((self.install_root / "runtime" / "AtlasHoldingEngine.exe").exists())

    def test_rejects_missing_approval_or_tampered_executable(self):
        with self.assertRaises(RuntimePayloadError):
            install_pinned_runtime_pair(
                self.source, self.install_root, self.pins, approved=False)
        (self.source / "AtlasHoldingEngine.exe").write_bytes(b"tampered")
        with self.assertRaises(RuntimePayloadError):
            install_pinned_runtime_pair(
                self.source, self.install_root, self.pins, approved=True)
        self.assertFalse((self.install_root / "runtime").exists())

    def test_existing_runtime_integrity_failure_never_overwrites(self):
        first = install_pinned_runtime_pair(
            self.source, self.install_root, self.pins, approved=True)
        old_engine = first.directory / "AtlasHoldingEngine.exe"
        old_engine.write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimePayloadError, "explicit repair"):
            install_pinned_runtime_pair(
                self.source, self.install_root, self.pins, approved=True)
        self.assertEqual(old_engine.read_bytes(), b"corrupt")

    def test_pin_or_source_missing_denies_every_write(self):
        wrong = {**self.pins, "AtlasHoldingEngine.exe": "0" * 64}
        with self.assertRaises(RuntimePayloadError):
            install_pinned_runtime_pair(
                self.source, self.install_root, wrong, approved=True)
        with self.assertRaises(RuntimePayloadError):
            install_pinned_runtime_pair(
                self.source, self.install_root,
                {"AtlasHoldingControlCenter.exe": self.pins[FILES[0]]},
                approved=True)
        self.assertFalse((self.install_root / "runtime").exists())

    def test_cancel_before_first_file_does_not_install(self):
        with self.assertRaisesRegex(RuntimePayloadError, "cancelled"):
            install_pinned_runtime_pair(
                self.source, self.install_root, self.pins, approved=True,
                cancelled=lambda: True)
        self.assertFalse((self.install_root / "runtime").exists())

    def test_cancel_during_second_chunk_cleans_partial_staging(self):
        calls = 0
        def stop_during_copy():
            nonlocal calls
            calls += 1
            return calls >= 5
        with self.assertRaisesRegex(RuntimePayloadError, "cancelled"):
            install_pinned_runtime_pair(
                self.source, self.install_root, self.pins, approved=True,
                cancelled=stop_during_copy)
        parent = self.install_root / "runtime"
        if parent.exists():
            self.assertEqual(list(parent.iterdir()), [])

    def test_cancel_after_existing_pair_does_not_report_reused(self):
        install_pinned_runtime_pair(
            self.source, self.install_root, self.pins, approved=True)
        with self.assertRaisesRegex(RuntimePayloadError, "cancelled"):
            install_pinned_runtime_pair(
                self.source, self.install_root, self.pins, approved=True,
                cancelled=lambda: True)

    def test_runtime_rejects_linked_binary(self):
        backup = self.source / "AtlasHoldingEngine.original"
        (self.source / FILES[1]).rename(backup)
        try:
            (self.source / FILES[1]).symlink_to(backup)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation disabled")
        with self.assertRaises(RuntimePayloadError):
            verify_runtime_payload(self.source, self.pins)


if __name__ == "__main__":
    unittest.main()

"""Exact verified runtime pair staging never executes or overwrites EXEs."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.native_runtime_stage import (
    NativePin, NativeRuntimeError, stage_native_runtime_pair,
)


FILES = ("AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe")


class NativeRuntimeStageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.source = self.root / "publisher"
        self.source.mkdir()
        self.destination = self.root / "installed"
        self.data = {
            FILES[0]: b"Control Center executable payload" * 40,
            FILES[1]: b"Autonomous engine executable payload" * 50,
        }
        for name, content in self.data.items():
            (self.source / name).write_bytes(content)
        self.pins = {
            name: NativePin(hashlib.sha256(data).hexdigest(), len(data))
            for name, data in self.data.items()
        }

    def stage(self, **changes):
        options = dict(source=self.source, destination=self.destination,
                       pins=self.pins, approved=True)
        options.update(changes)
        return stage_native_runtime_pair(**options)

    def test_two_files_published_atomically_and_reused_unchanged(self):
        first = self.stage()
        self.assertEqual(first.status, "STAGED_UNREGISTERED")
        self.assertEqual({p.name for p in first.location.iterdir()},
                         {*FILES, ".atlas-runtime-receipt.json"})
        self.assertEqual((first.location / FILES[0]).read_bytes(), self.data[FILES[0]])
        self.assertEqual((first.location / FILES[1]).read_bytes(), self.data[FILES[1]])
        again = self.stage()
        self.assertEqual(again.status, "VERIFIED_REUSED_UNREGISTERED")
        self.assertEqual(again.location, first.location)

    def test_requires_approval_even_for_verified_publisher_files(self):
        result = self.stage(approved=False)
        self.assertEqual(result.status, "APPROVAL_REQUIRED")
        self.assertFalse(self.destination.exists())

    def test_tampered_second_binary_cannot_leave_partial_first(self):
        (self.source / FILES[1]).write_bytes(b"unauthorized change")
        with self.assertRaisesRegex(NativeRuntimeError, "size mismatch"):
            self.stage()
        self.assertFalse(self.destination.exists())

    def test_mutating_staged_pair_fails_closed_without_reinstall(self):
        first = self.stage()
        (first.location / FILES[1]).write_bytes(b"tampered")
        with self.assertRaises(NativeRuntimeError):
            self.stage()
        self.assertEqual((first.location / FILES[1]).read_bytes(), b"tampered")

    def test_source_tampering_detected_even_if_candidate_exists(self):
        first = self.stage()
        (self.source / FILES[0]).write_bytes(b"changed publisher")
        with self.assertRaises(NativeRuntimeError):
            self.stage()
        self.assertTrue(first.location.exists())

    def test_missing_pin_and_wrong_approval_type_rejected(self):
        with self.assertRaises(NativeRuntimeError):
            self.stage(pins={FILES[0]: self.pins[FILES[0]]})
        with self.assertRaises(NativeRuntimeError):
            self.stage(approved=1)
        with self.assertRaises(NativeRuntimeError):
            self.stage(destination=Path("relative"))

    def test_duplicate_source_target_tree_rejected(self):
        with self.assertRaises(NativeRuntimeError):
            self.stage(destination=self.source / "nested")
        with self.assertRaises(NativeRuntimeError):
            self.stage(destination=self.source)

    def test_symlinked_binary_refused(self):
        (self.source / FILES[1]).unlink()
        try:
            (self.source / FILES[1]).symlink_to(self.source / FILES[0])
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable")
        with self.assertRaises(NativeRuntimeError):
            self.stage()
        self.assertFalse(self.destination.exists())

    def test_failed_second_copy_does_not_publish_partial_candidate(self):
        import shutil
        original = shutil.copyfile
        calls = [0]
        def broken_copy(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("simulated disk full")
            return original(*args, **kwargs)
        with patch("holding.native_runtime_stage.shutil.copyfile",
                   side_effect=broken_copy):
            with self.assertRaises(OSError):
                self.stage()
        candidates = self.destination / "runtime-candidates"
        self.assertFalse(any(candidates.iterdir()))


if __name__ == "__main__":
    unittest.main()

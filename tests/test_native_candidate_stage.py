"""Nonrelease staging checks using real file bytes and filesystem mutations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.native_candidate_stage import (
    stage_native_candidate, verify_native_candidate,
)
from holding.release_evidence import (
    ReleaseEvidenceError, observe_native_release_files,
)

SHA = "a" * 40
UPSTREAM = {
    **{f"pr{i}": "b" * 40 for i in range(1, 9)},
    "pr11": "c" * 40, "pr12": "d" * 40,
}
BINARIES = (
    "AtlasHoldingSetup.exe",
    "AtlasHoldingControlCenter.exe",
    "AtlasHoldingEngine.exe",
)


class NativeCandidateStagingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.source = self.root / "build"
        self.source.mkdir()
        self.dest = self.root / "candidate"
        for idx, name in enumerate(BINARIES):
            (self.source / name).write_bytes(bytes([65 + idx]) * (128 + idx))

    def stage(self, **changes):
        settings = {"source_sha": SHA, "upstream_shas": UPSTREAM}
        settings.update(changes)
        return stage_native_candidate(self.source, self.dest, **settings)

    def test_stages_exact_three_files_with_measured_index_and_no_release_claim(self):
        observed = observe_native_release_files(self.source)
        result = self.stage()
        self.assertFalse(result.release_ready)
        self.assertEqual(result.directory, self.dest)
        self.assertEqual(result.source_sha, SHA)
        self.assertEqual(observe_native_release_files(self.dest), observed)
        index_file = self.dest / "candidate-index.json"
        self.assertEqual(result.index_sha256,
                         hashlib.sha256(index_file.read_bytes()).hexdigest())
        index = json.loads(index_file.read_bytes())
        self.assertIs(index["release_ready"], False)
        self.assertEqual(index["source_sha"], SHA)
        self.assertEqual(index["upstream_shas"], UPSTREAM)
        self.assertEqual(
            set(e["name"] for e in index["artifacts"]), set(BINARIES))
        self.assertEqual(
            set(f.name for f in self.dest.iterdir()),
            set(BINARIES) | {"candidate-index.json"},
        )
        self.assertFalse((self.root / ".candidate.staging-lock").exists())

    def test_never_overwrites_existing_candidate_or_existing_lock(self):
        self.dest.mkdir()
        (self.dest / "notes.txt").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseEvidenceError, "Unsafe"):
            self.stage()
        self.assertEqual((self.dest / "notes.txt").read_text(), "keep")
        self.dest.rmdir() if not any(self.dest.iterdir()) else None
        (self.dest / "notes.txt").unlink()
        self.dest.rmdir()
        lock = self.root / ".candidate.staging-lock"
        lock.write_text("belongs to concurrent runner")
        with self.assertRaisesRegex(ReleaseEvidenceError, "already staging"):
            self.stage()
        self.assertEqual(lock.read_text(), "belongs to concurrent runner")
        self.assertFalse(self.dest.exists())

    def test_missing_source_or_required_upstream_never_creates_output(self):
        (self.source / "AtlasHoldingEngine.exe").unlink()
        with self.assertRaises(ReleaseEvidenceError):
            self.stage()
        self.assertFalse(self.dest.exists())
        (self.source / "AtlasHoldingEngine.exe").write_bytes(b"engine")
        with self.assertRaises(ReleaseEvidenceError):
            self.stage(upstream_shas={"pr11": "c" * 40})
        self.assertFalse(self.dest.exists())

    def test_rejects_missing_observed_hash_after_source_changes(self):
        import holding.native_candidate_stage as target
        real_observe = target.observe_native_release_files
        call_count = 0

        def changed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                (self.source / "AtlasHoldingEngine.exe").write_bytes(b"tampered")
            return real_observe(*args, **kwargs)

        with patch.object(target, "observe_native_release_files",
                          side_effect=changed):
            with self.assertRaisesRegex(ReleaseEvidenceError, "changed"):
                self.stage()
        self.assertFalse(self.dest.exists())
        self.assertFalse((self.root / ".candidate.staging-lock").exists())

    def test_relative_and_symlinked_paths_refused(self):
        with self.assertRaises(ReleaseEvidenceError):
            stage_native_candidate(
                Path("relative"), self.dest,
                source_sha=SHA, upstream_shas=UPSTREAM)
        link = self.root / "fake-build"
        try:
            link.symlink_to(self.source, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Host does not support test symlinks")
        with self.assertRaises(ReleaseEvidenceError):
            stage_native_candidate(
                link, self.dest, source_sha=SHA, upstream_shas=UPSTREAM)

    def test_staged_candidate_can_be_reverified_by_trusted_installer(self):
        staged = self.stage()
        observed = verify_native_candidate(
            staged.directory, expected_source_sha=SHA,
            expected_upstream_shas=UPSTREAM,
        )
        self.assertEqual(observed, staged)
        (self.dest / "AtlasHoldingEngine.exe").write_bytes(b"tampered")
        with self.assertRaisesRegex(ReleaseEvidenceError, "changed"):
            verify_native_candidate(
                staged.directory, expected_source_sha=SHA,
                expected_upstream_shas=UPSTREAM,
            )

    def test_forged_index_or_stale_upstream_cannot_be_adopted(self):
        staged = self.stage()
        index_file = staged.directory / "candidate-index.json"
        values = json.loads(index_file.read_bytes())
        values["release_ready"] = True
        index_file.write_text(json.dumps(values), encoding="utf-8")
        with self.assertRaisesRegex(ReleaseEvidenceError, "falsely certified"):
            verify_native_candidate(
                staged.directory, expected_source_sha=SHA,
                expected_upstream_shas=UPSTREAM,
            )
        values["release_ready"] = False
        index_file.write_text(json.dumps(values), encoding="utf-8")
        with self.assertRaisesRegex(ReleaseEvidenceError, "stale"):
            verify_native_candidate(
                staged.directory, expected_source_sha="0" * 40,
                expected_upstream_shas=UPSTREAM,
            )
        with self.assertRaisesRegex(ReleaseEvidenceError, "stale"):
            verify_native_candidate(
                staged.directory, expected_source_sha=SHA,
                expected_upstream_shas={**UPSTREAM, "pr11": "e" * 40},
            )

    def test_index_duplicate_json_keys_and_missing_engine_fail_closed(self):
        staged = self.stage()
        idx = staged.directory / "candidate-index.json"
        idx.write_bytes(b'{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ReleaseEvidenceError):
            verify_native_candidate(
                staged.directory, expected_source_sha=SHA,
                expected_upstream_shas=UPSTREAM,
            )
        self.assertFalse((self.root / ".candidate.staging-lock").exists())

    def test_does_not_confuse_self_signed_index_with_release_manifest(self):
        result = self.stage()
        document = json.loads((result.directory / "candidate-index.json").read_text())
        self.assertNotIn("gates", document)
        self.assertNotIn("signing", document)
        self.assertEqual(document["schema_version"], 1)
        self.assertFalse(document["release_ready"])


if __name__ == "__main__":
    unittest.main()

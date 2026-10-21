import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from holding.release_evidence import (
    ArtifactObservation, ReleaseEvidenceError, validate_release_manifest,
    observe_native_release_files,
)


SHA = "a" * 40
UPSTREAM = {
    **{f"pr{i}": "b" * 40 for i in range(1, 9)},
    "pr11": "c" * 40, "pr12": "d" * 40,
}
OBSERVED = {
    "AtlasHoldingSetup.exe": ArtifactObservation("d" * 64, 100),
    "AtlasHoldingControlCenter.exe": ArtifactObservation("1" * 64, 200),
    "AtlasHoldingEngine.exe": ArtifactObservation("2" * 64, 300),
    "manifest.json": ArtifactObservation("e" * 64, 100),
}
GATES = (
    "windows-clean-install", "offline-after-provisioning", "e2e-roadmap",
    "native-runtime-ipc", "windows-acl-isolation",
)
GATE_EVIDENCE = {name: "f" * 64 for name in GATES}


def manifest():
    return {
        "schema_version": 1, "source_sha": SHA, "platform": "windows-real",
        "upstream_shas": dict(UPSTREAM),
        "artifacts": [
            {"name": name, "sha256": observed.sha256,
             "size_bytes": observed.size_bytes}
            for name, observed in OBSERVED.items()
        ],
        "gates": [{
            "name": name, "status": "PASS", "source_sha": SHA,
            "platform": "windows-real", "evidence_sha256": "f" * 64,
        } for name in GATES],
        "signing": {"signed": False, "signature_evidence_sha256": None,
                    "smartscreen_warning": True},
    }


def validate(payload, *, observed=OBSERVED, gate_evidence=GATE_EVIDENCE,
             signature_evidence=None):
    return validate_release_manifest(
        json.dumps(payload).encode(), expected_source_sha=SHA,
        expected_upstream_shas=UPSTREAM, observed_artifacts=observed,
        observed_gate_evidence=gate_evidence,
        observed_signature_evidence_sha256=signature_evidence,
        required_gates=GATES,
    )


class ReleaseEvidenceTests(unittest.TestCase):
    def test_exact_candidate_is_publishable(self):
        candidate = validate(manifest())
        self.assertTrue(candidate.publishable)
        self.assertFalse(candidate.signed)

    def test_stale_source_or_upstream_is_rejected(self):
        value = manifest(); value["source_sha"] = "0" * 40
        with self.assertRaisesRegex(ReleaseEvidenceError, "exact-SHA"):
            validate(value)
        value = manifest(); value["upstream_shas"]["pr8"] = "0" * 40
        with self.assertRaisesRegex(ReleaseEvidenceError, "stale upstream"):
            validate(value)

    def test_missing_required_integration_or_guardrail_pr_is_not_releasable(self):
        for missing in ("pr2", "pr3", "pr8", "pr11", "pr12"):
            payload = manifest()
            del payload["upstream_shas"][missing]
            expected = dict(UPSTREAM)
            del expected[missing]
            with self.subTest(missing=missing), self.assertRaisesRegex(
                    ReleaseEvidenceError, "trusted release evidence"):
                validate_release_manifest(
                    json.dumps(payload).encode(),
                    expected_source_sha=SHA,
                    expected_upstream_shas=expected,
                    observed_artifacts=OBSERVED,
                    observed_gate_evidence=GATE_EVIDENCE,
                    observed_signature_evidence_sha256=None,
                    required_gates=GATES,
                )

    def test_simulated_windows_evidence_is_rejected(self):
        value = manifest(); value["platform"] = "windows-simulated"
        with self.assertRaisesRegex(ReleaseEvidenceError, "Windows evidence"):
            validate(value)

    def test_artifact_hash_must_match_observation(self):
        value = manifest(); value["artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ReleaseEvidenceError, "observed"):
            validate(value)

    def test_artifact_size_must_match_observation(self):
        value = manifest(); value["artifacts"][0]["size_bytes"] = 101
        with self.assertRaisesRegex(ReleaseEvidenceError, "observed"):
            validate(value)

    def test_missing_or_failed_gate_is_rejected(self):
        value = manifest(); value["gates"].pop()
        with self.assertRaisesRegex(ReleaseEvidenceError, "incomplete"):
            validate(value)

    def test_gate_digest_must_match_observed_evidence(self):
        value = manifest(); value["gates"][0]["evidence_sha256"] = "0" * 64
        with self.assertRaisesRegex(ReleaseEvidenceError, "differs from observation"):
            validate(value)
        value = manifest(); value["gates"][0]["status"] = "FAIL"
        with self.assertRaisesRegex(ReleaseEvidenceError, "PASS"):
            validate(value)

    def test_setup_without_real_viewer_or_engine_is_unpublishable(self):
        for missing in ("AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe"):
            payload = manifest()
            payload["artifacts"] = [
                item for item in payload["artifacts"]
                if item["name"] != missing
            ]
            observed = dict(OBSERVED)
            del observed[missing]
            with self.subTest(missing=missing), self.assertRaisesRegex(
                    ReleaseEvidenceError, "requires observed setup"):
                validate(payload, observed=observed)

    def test_operator_cannot_omit_native_acl_or_ipc_release_gates(self):
        for gate in ("native-runtime-ipc", "windows-acl-isolation"):
            gates = tuple(item for item in GATES if item != gate)
            with self.subTest(gate=gate), self.assertRaises(
                    ReleaseEvidenceError):
                validate_release_manifest(
                    json.dumps(manifest()).encode(),
                    expected_source_sha=SHA,
                    expected_upstream_shas=UPSTREAM,
                    observed_artifacts=OBSERVED,
                    observed_gate_evidence=GATE_EVIDENCE,
                    observed_signature_evidence_sha256=None,
                    required_gates=gates,
                )

    def test_unsigned_build_requires_smartscreen_warning(self):
        value = manifest(); value["signing"]["smartscreen_warning"] = False
        with self.assertRaisesRegex(ReleaseEvidenceError, "SmartScreen"):
            validate(value)

    def test_fake_signature_is_rejected(self):
        value = manifest(); value["signing"]["signed"] = True
        with self.assertRaisesRegex(ReleaseEvidenceError, "signature evidence"):
            validate(value)

    def test_signature_is_bound_to_observed_evidence(self):
        value = manifest()
        value["signing"].update(
            signed=True, signature_evidence_sha256="1" * 64,
            smartscreen_warning=False,
        )
        with self.assertRaisesRegex(ReleaseEvidenceError, "differs from observation"):
            validate(value, signature_evidence="2" * 64)
        candidate = validate(value, signature_evidence="1" * 64)
        self.assertTrue(candidate.signed)

    def test_release_observation_hashes_three_real_files_not_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            data = {
                "AtlasHoldingSetup.exe": b"S" * 27,
                "AtlasHoldingControlCenter.exe": b"G" * 37,
                "AtlasHoldingEngine.exe": b"E" * 41,
            }
            for name, content in data.items():
                (root / name).write_bytes(content)
            observed = observe_native_release_files(root)
            self.assertEqual(set(observed), set(data))
            for name, content in data.items():
                self.assertEqual(
                    observed[name],
                    ArtifactObservation(hashlib.sha256(content).hexdigest(),
                                        len(content)),
                )
            (root / "AtlasHoldingEngine.exe").write_bytes(b"MODIFIED")
            self.assertNotEqual(
                observe_native_release_files(root)["AtlasHoldingEngine.exe"],
                observed["AtlasHoldingEngine.exe"],
            )

    def test_release_observation_fails_if_runtime_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "AtlasHoldingSetup.exe").write_bytes(b"setup")
            with self.assertRaises(ReleaseEvidenceError):
                observe_native_release_files(root)
            with self.assertRaises(ReleaseEvidenceError):
                observe_native_release_files(Path("relative"))

    def test_release_observation_does_not_follow_symlinked_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name in ("AtlasHoldingSetup.exe", "AtlasHoldingEngine.exe"):
                (root / name).write_bytes(b"some bytes")
            link = root / "AtlasHoldingControlCenter.exe"
            try:
                link.symlink_to(root / "AtlasHoldingEngine.exe")
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation disabled")
            with self.assertRaises(ReleaseEvidenceError):
                observe_native_release_files(root)

    def test_duplicate_manifest_keys_are_rejected(self):
        raw = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(ReleaseEvidenceError, "duplicate JSON"):
            validate_release_manifest(
                raw, expected_source_sha=SHA, expected_upstream_shas=UPSTREAM,
                observed_artifacts=OBSERVED,
                observed_gate_evidence=GATE_EVIDENCE,
                observed_signature_evidence_sha256=None,
                required_gates=GATES,
            )


if __name__ == "__main__":
    unittest.main()

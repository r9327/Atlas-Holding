import json
import unittest

from holding.release_evidence import ReleaseEvidenceError, validate_release_manifest


SHA = "a" * 40
UPSTREAM = {"pr8": "b" * 40, "pr10": "c" * 40}
OBSERVED = {"AtlasHoldingSetup.exe": "d" * 64, "manifest.json": "e" * 64}
GATES = ("windows-clean-install", "offline-after-provisioning", "e2e-roadmap")


def manifest():
    return {
        "schema_version": 1, "source_sha": SHA, "platform": "windows-real",
        "upstream_shas": dict(UPSTREAM),
        "artifacts": [
            {"name": name, "sha256": digest, "size_bytes": 100}
            for name, digest in OBSERVED.items()
        ],
        "gates": [{
            "name": name, "status": "PASS", "source_sha": SHA,
            "platform": "windows-real", "evidence_sha256": "f" * 64,
        } for name in GATES],
        "signing": {"signed": False, "signature_evidence_sha256": None,
                    "smartscreen_warning": True},
    }


def validate(payload):
    return validate_release_manifest(
        json.dumps(payload).encode(), expected_source_sha=SHA,
        expected_upstream_shas=UPSTREAM, observed_artifacts=OBSERVED,
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

    def test_simulated_windows_evidence_is_rejected(self):
        value = manifest(); value["platform"] = "windows-simulated"
        with self.assertRaisesRegex(ReleaseEvidenceError, "Windows evidence"):
            validate(value)

    def test_artifact_hash_must_match_observation(self):
        value = manifest(); value["artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ReleaseEvidenceError, "observed"):
            validate(value)

    def test_missing_or_failed_gate_is_rejected(self):
        value = manifest(); value["gates"].pop()
        with self.assertRaisesRegex(ReleaseEvidenceError, "incomplete"):
            validate(value)
        value = manifest(); value["gates"][0]["status"] = "FAIL"
        with self.assertRaisesRegex(ReleaseEvidenceError, "PASS"):
            validate(value)

    def test_unsigned_build_requires_smartscreen_warning(self):
        value = manifest(); value["signing"]["smartscreen_warning"] = False
        with self.assertRaisesRegex(ReleaseEvidenceError, "SmartScreen"):
            validate(value)

    def test_fake_signature_is_rejected(self):
        value = manifest(); value["signing"]["signed"] = True
        with self.assertRaisesRegex(ReleaseEvidenceError, "signature evidence"):
            validate(value)


if __name__ == "__main__":
    unittest.main()

import json
import unittest

from holding.roadmap_contract import RoadmapContractError, validate_roadmap_plan


SHA = "a" * 40
DIGEST = "b" * 64


def plan():
    return {
        "schema_version": 1, "repository": "r9327/Dof-Atlas",
        "snapshot_sha": SHA, "source_uri": "ROADMAP.md", "source_digest": DIGEST,
        "requirements": [
            {"id": "req-1", "statement": "Implement core"},
            {"id": "req-2", "statement": "Prove integration"},
        ],
        "lots": [
            {"id": "lot-1", "title": "Core", "dependencies": [],
             "requirement_ids": ["req-1"], "acceptance": ["targeted tests pass"],
             "autonomy": "AUTO", "state": "READY",
             "external_evidence_required": False, "external_review_pending": False},
            {"id": "lot-2", "title": "Integration", "dependencies": ["lot-1"],
             "requirement_ids": ["req-2"], "acceptance": ["exact SHA CI passes"],
             "autonomy": "AUTO_WITH_STANDING_GRANT", "state": "TRIAGED",
             "external_evidence_required": True, "external_review_pending": True},
        ],
    }


def validate(value):
    return validate_roadmap_plan(
        json.dumps(value).encode(), expected_repository="r9327/Dof-Atlas",
        expected_snapshot_sha=SHA, expected_source_digest=DIGEST,
    )


class RoadmapContractTests(unittest.TestCase):
    def test_complete_acyclic_plan_is_immutable_and_reports_ready_lots(self):
        result = validate(plan())
        self.assertEqual(result.initially_ready_lots(), ("lot-1",))
        with self.assertRaises(AttributeError):
            result.snapshot_sha = "c" * 40

    def test_source_identity_is_exact(self):
        value = plan(); value["source_digest"] = "c" * 64
        with self.assertRaisesRegex(RoadmapContractError, "trusted source"):
            validate(value)

    def test_every_requirement_must_be_covered(self):
        value = plan(); value["lots"][1]["requirement_ids"] = ["req-1"]
        with self.assertRaisesRegex(RoadmapContractError, "exhaustively"):
            validate(value)

    def test_unknown_requirement_is_rejected(self):
        value = plan(); value["lots"][1]["requirement_ids"] = ["req-2", "missing"]
        with self.assertRaisesRegex(RoadmapContractError, "unknown requirement"):
            validate(value)

    def test_unknown_self_or_cyclic_dependencies_are_rejected(self):
        value = plan(); value["lots"][0]["dependencies"] = None
        with self.assertRaisesRegex(RoadmapContractError, "lot dependencies"):
            validate(value)
        value = plan(); value["lots"][1]["dependencies"] = ["missing"]
        with self.assertRaisesRegex(RoadmapContractError, "invalid dependency"):
            validate(value)
        value = plan(); value["lots"][0]["dependencies"] = ["lot-2"]
        value["lots"][0]["state"] = "TRIAGED"
        with self.assertRaisesRegex(RoadmapContractError, "cycle"):
            validate(value)

    def test_done_or_other_advanced_state_cannot_be_imported(self):
        value = plan(); value["lots"][0]["state"] = "DONE"
        with self.assertRaisesRegex(RoadmapContractError, "initial lot"):
            validate(value)

    def test_ready_lot_cannot_have_dependencies_or_human_blocker(self):
        value = plan(); value["lots"][1]["state"] = "READY"
        with self.assertRaisesRegex(RoadmapContractError, "not executable"):
            validate(value)
        value = plan(); value["lots"][0]["autonomy"] = "REQUIRES_DECISION"
        with self.assertRaises(RoadmapContractError):
            validate(value)

    def test_external_review_starts_pending_exactly_when_required(self):
        value = plan(); value["lots"][1]["external_review_pending"] = False
        with self.assertRaisesRegex(RoadmapContractError, "start pending"):
            validate(value)

    def test_unknown_fields_and_percentages_are_rejected(self):
        value = plan(); value["lots"][0]["progress_percent"] = 100
        with self.assertRaisesRegex(RoadmapContractError, "shape"):
            validate(value)


if __name__ == "__main__":
    unittest.main()

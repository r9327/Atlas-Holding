import json
from pathlib import Path
import tempfile
import unittest

from holding.project_mission_gate import (
    ProjectMissionGateError, import_verified_project_missions,
)
from holding.project_registry import ProjectRegistry
from holding.project_plan_binding import ProjectPlanBindingError

SHA = "a" * 40
DIGEST = "b" * 64
PROFILE = {
    "schema": "project-profile/v1", "name": "dofus-atlas",
    "repository": "r9327/Dof-Atlas", "branch": "feat/atlas-work",
    "checkout_dir": "dofus-atlas-worktree",
    "commands": {"test": ["python", "-m", "unittest", "discover"]},
    "adapters": ["doctor"],
}


def planner():
    return {
        "schema_version": 1, "repository": "r9327/Dof-Atlas",
        "snapshot_sha": SHA, "source_uri": "ROADMAP.md", "source_digest": DIGEST,
        "requirements": [{"id": "req-1", "statement": "Code"},
                         {"id": "req-2", "statement": "Test"}],
        "lots": [
            {"id": "lot-1", "title": "Code", "dependencies": [],
             "requirement_ids": ["req-1"], "acceptance": ["compile without regression"],
             "autonomy": "AUTO", "state": "READY",
             "external_evidence_required": False, "external_review_pending": False},
            {"id": "lot-2", "title": "Prove", "dependencies": ["lot-1"],
             "requirement_ids": ["req-2"], "acceptance": ["full CI signed proof"],
             "autonomy": "AUTO_WITH_STANDING_GRANT", "state": "TRIAGED",
             "external_evidence_required": True, "external_review_pending": True},
        ],
    }


def operations():
    return {"repo": "r9327/Dof-Atlas", "missions": [
        {"id": "lot-1", "operation": "code",
         "acceptance": ["compile without regression"]},
        {"id": "lot-2", "operation": "read_ci", "depends_on": ["lot-1"],
         "acceptance": ["full CI signed proof"], "external_required": True},
    ]}


class ProjectMissionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = ProjectRegistry(Path(self.temp.name) / "registry")
        self.entry = self.registry.register(json.dumps(PROFILE))
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        self.roadmap = planner()
        self.missions = operations()
        self.calls = []

    def invoke(self, **overrides):
        kwargs = {
            "expected_profile_digest": self.entry.digest,
            "expected_snapshot_sha": SHA,
            "expected_source_digest": DIGEST,
            "runtime_repository": "r9327/Dof-Atlas",
            "source": "trusted:roadmap",
            "trusted_importer": self._record,
        }
        kwargs.update(overrides)
        return import_verified_project_missions(
            self.registry, json.dumps(self.roadmap).encode("utf-8"),
            json.dumps(self.missions), **kwargs,
        )

    def _record(self, raw, *, source):
        self.calls.append((raw, source))
        return {"count": len(json.loads(raw)["missions"])}

    def test_matching_complete_plan_invokes_trusted_importer_once(self):
        self.assertEqual(self.invoke(), {"count": 2})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], "trusted:roadmap")

    def test_wrong_repository_denied_before_import(self):
        self.missions["repo"] = "example/other"
        with self.assertRaisesRegex(ProjectMissionGateError, "another project"):
            self.invoke()
        self.assertFalse(self.calls)

    def test_missing_or_extra_lots_refused(self):
        self.missions["missions"].pop()
        with self.assertRaisesRegex(ProjectMissionGateError, "omits"):
            self.invoke()
        self.assertFalse(self.calls)
        self.missions["missions"] = operations()["missions"] + [{
            "id": "lot-3", "operation": "code", "acceptance": ["unapproved"]
        }]
        with self.assertRaisesRegex(ProjectMissionGateError, "approved planner lots"):
            self.invoke()
        self.assertFalse(self.calls)

    def test_dependencies_and_acceptance_cannot_be_rewritten(self):
        self.missions["missions"][1]["depends_on"] = []
        with self.assertRaisesRegex(ProjectMissionGateError, "differs"):
            self.invoke()
        self.missions = operations()
        self.missions["missions"][0]["acceptance"] = ["no tests needed"]
        with self.assertRaisesRegex(ProjectMissionGateError, "differs"):
            self.invoke()
        self.assertFalse(self.calls)

    def test_denies_auto_merge_and_privileged_command(self):
        self.missions["missions"][0]["operation"] = "merge"
        with self.assertRaisesRegex(ProjectMissionGateError, "privileged operation"):
            self.invoke()
        self.assertFalse(self.calls)

    def test_restricted_planner_lot_cannot_be_imported(self):
        self.roadmap["lots"][1]["autonomy"] = "REQUIRES_DECISION"
        with self.assertRaisesRegex(ProjectMissionGateError, "restricted or dependent"):
            self.invoke()
        self.assertFalse(self.calls)

    def test_missing_changed_selection_refuses_write(self):
        self.registry.clear_selection()
        with self.assertRaises(ProjectPlanBindingError):
            self.invoke()
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        with self.assertRaises(ProjectPlanBindingError):
            self.invoke(expected_profile_digest="f" * 64)
        self.assertFalse(self.calls)

    def test_source_exact_sha_and_digest_mandatory(self):
        with self.assertRaises(ProjectPlanBindingError):
            self.invoke(expected_snapshot_sha="c" * 40)
        with self.assertRaises(ProjectPlanBindingError):
            self.invoke(expected_source_digest="d" * 64)
        self.assertFalse(self.calls)

    def test_duplicate_keys_and_invalid_operations_rejected(self):
        raw = '{"repo":"r9327/Dof-Atlas","repo":"other","missions":[]}'
        with self.assertRaisesRegex(ProjectMissionGateError, "duplicate"):
            import_verified_project_missions(
                self.registry, json.dumps(self.roadmap).encode(),
                raw, expected_profile_digest=self.entry.digest,
                expected_snapshot_sha=SHA, expected_source_digest=DIGEST,
                runtime_repository="r9327/Dof-Atlas",
                source="trusted:roadmap", trusted_importer=self._record)
        self.assertFalse(self.calls)

    def test_installed_runtime_repository_must_match_selected_project(self):
        with self.assertRaisesRegex(ProjectMissionGateError, "installed engine"):
            self.invoke(runtime_repository="example/another")
        self.assertEqual(self.calls, [])

    def test_planner_blocked_lot_cannot_be_silently_imported_ready(self):
        self.roadmap["lots"][1]["state"] = "BLOCKED"
        with self.assertRaisesRegex(ProjectMissionGateError, "restricted or dependent"):
            self.invoke()
        self.assertEqual(self.calls, [])

    def test_independent_lot_is_importable_when_another_is_blocked(self):
        self.roadmap["lots"][1]["state"] = "BLOCKED"
        self.missions["missions"].pop()
        self.assertEqual(self.invoke(), {"count": 1})
        self.assertEqual([m["id"] for m in json.loads(self.calls[0][0])["missions"]],
                         ["lot-1"])

    def test_restricted_lot_and_its_descendants_stay_out_of_import(self):
        self.roadmap["lots"][1]["autonomy"] = "REQUIRES_DECISION"
        self.roadmap["lots"][1]["state"] = "REQUIRES_DECISION"
        self.roadmap["requirements"].append({"id": "req-3", "statement": "Downstream"})
        self.roadmap["lots"].append({
            "id": "lot-3", "title": "Downstream", "dependencies": ["lot-2"],
            "requirement_ids": ["req-3"], "acceptance": ["prove downstream"],
            "autonomy": "AUTO", "state": "TRIAGED",
            "external_evidence_required": False, "external_review_pending": False,
        })
        self.missions["missions"] = [operations()["missions"][0]]
        self.assertEqual(self.invoke(), {"count": 1})
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        self.missions["missions"].append({
            "id": "lot-3", "operation": "code", "depends_on": ["lot-2"],
            "acceptance": ["prove downstream"],
        })
        with self.assertRaisesRegex(ProjectMissionGateError, "restricted or dependent"):
            self.invoke()
        self.assertFalse(self.calls)

    def test_no_independent_lots_cannot_be_imported(self):
        self.roadmap["lots"][0]["state"] = "BLOCKED"
        self.missions["missions"] = []
        with self.assertRaisesRegex(ProjectMissionGateError, "no independently importable"):
            self.invoke()
        self.assertFalse(self.calls)

    def test_user_decision_flag_cannot_be_hidden_in_auto_mission(self):
        self.missions["missions"][0]["product_decision_required"] = True
        with self.assertRaisesRegex(ProjectMissionGateError, "differs"):
            self.invoke()
        self.assertEqual(self.calls, [])

    def test_failing_importer_does_not_claim_completion(self):
        def blocked(raw, *, source):
            raise RuntimeError("transaction refused")
        with self.assertRaisesRegex(RuntimeError, "transaction refused"):
            self.invoke(trusted_importer=blocked)
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()

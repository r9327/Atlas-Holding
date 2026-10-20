"""Replans preserve all checkpoints; only untouched READY work can change."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest

from holding.autonomy import StandingGrant
from holding.roadmap_import import RoadmapImporter
from holding.roadmap_replan import SafeRoadmapReplanner
from holding.state_store import StateStore, StoreError

REPO = "r9327/Dof-Atlas"
SHA = "a" * 40


def document(*missions):
    return json.dumps({"repo": REPO, "missions": list(missions)})


A = {"id": "a", "operation": "code", "acceptance": ["Produce A"]}
B = {"id": "b", "operation": "code", "depends_on": ["a"],
     "acceptance": ["Produce B"]}
C = {"id": "c", "operation": "code", "depends_on": ["b"],
     "acceptance": ["Produce C"]}


class RoadmapReplanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = StateStore(Path(temp.name) / "state.sqlite")
        self.addCleanup(self.store.close)
        self.first = RoadmapImporter(self.store).import_text(
            document(A, B), source="initial")
        self.replan = SafeRoadmapReplanner(self.store)
        self.store.grant_for_onboarding(StandingGrant(
            REPO, frozenset({"code"}), int(time.time()) + 600))

    def test_add_ready_tasks_and_revise_unstarted_acceptance(self):
        b2 = dict(B, acceptance=["Produce B and verify its API"], priority=9)
        candidate = document(A, b2, C)
        preview = self.replan.preview(candidate)
        self.assertEqual(preview.added, ("c",))
        self.assertEqual(preview.revised, ("b",))
        self.assertEqual(preview.preserved, ("a",))
        self.assertFalse(preview.blocked)
        self.assertEqual(len(self.store.missions()), 2)
        applied = self.replan.apply(candidate, source="new-directive")
        self.assertTrue(applied.applied)
        self.assertEqual({m.id: m.state for m in self.store.missions()},
                         {"a": "READY", "b": "READY", "c": "READY"})
        self.assertEqual({m.id: m.priority for m in self.store.missions()}["b"], 9)
        self.assertEqual(self.replan.apply(candidate, source="repeat").applied, False)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_amendments").fetchone()[0], 1)

    def test_incremental_direction_preserves_unmentioned_tasks(self):
        change = self.replan.append_direction(
            document(C), source="ui:one-new-goal")
        self.assertEqual(change.added, ("c",))
        self.assertEqual(change.preserved, ("a", "b"))
        self.assertEqual({m.id for m in self.store.missions()},
                         {"a", "b", "c"})
        repeat = self.replan.append_direction(
            document(C), source="ui:repeat-goal")
        self.assertFalse(repeat.applied)
        self.assertEqual(len(self.store.missions()), 3)
        source = self.store.db.execute(
            "SELECT source FROM roadmap_versions WHERE version=?",
            (change.version,)).fetchone()[0]
        self.assertIn("delta_sha256:", source)

    def test_incremental_direction_rejects_dependency_absent_from_saved_dag(self):
        missing = dict(C, depends_on=["never-imported"])
        before = len(self.store.missions())
        with self.assertRaisesRegex(StoreError, "Unknown dependency"):
            self.replan.append_direction(
                document(missing), source="ui:bad-dependency")
        self.assertEqual(len(self.store.missions()), before)

    def test_incremental_direction_cannot_modify_started_mission(self):
        self.assertEqual(self.store.claim_next(online=False).mission_id, "a")
        self.store.checkpoint("a", SHA)
        changed = dict(A, acceptance=["New and incompatible acceptance"])
        with self.assertRaisesRegex(StoreError, "ALREADY_STARTED"):
            self.replan.append_direction(
                document(changed), source="ui:unsafe-rewrite")
        self.assertEqual(self.store.missions()[0].candidate_sha, SHA)

    def test_omitted_mission_is_not_silently_cancelled(self):
        with self.assertRaisesRegex(StoreError, "OMITTED"):
            self.replan.apply(document(A), source="remove")
        self.assertEqual(len(self.store.missions()), 2)

    def test_changed_active_or_done_mission_is_refused(self):
        self.assertEqual(self.store.claim_next(online=False).mission_id, "a")
        self.store.checkpoint("a", SHA)
        with self.assertRaisesRegex(StoreError, "ALREADY_STARTED"):
            self.replan.apply(
                document(dict(A, acceptance=["New terms"]), B), source="unsafe")
        self.assertEqual(self.store.missions()[0].candidate_sha, SHA)

    def test_cycle_revision_is_atomic(self):
        modified = dict(A, depends_on=["b"])
        with self.assertRaisesRegex(StoreError, "Dependency cycle"):
            self.replan.apply(document(modified, B), source="bad-dag")
        self.assertEqual(self.store.missions()[0].dependencies, ())
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_amendments").fetchone()[0], 0)

    def test_operations_cannot_be_swapped_by_new_direction(self):
        modified = dict(A, operation="merge")
        with self.assertRaisesRegex(StoreError, "OPERATION_CHANGE"):
            self.replan.apply(document(modified, B), source="unsafe")
        self.assertEqual(self.store.missions()[0].operation, "code")

    def test_original_import_api_rejects_replay_after_revision(self):
        newer = dict(B, acceptance=["B verified by regression tests"])
        self.replan.apply(document(A, newer), source="changed")
        with self.assertRaisesRegex(StoreError, "Stale roadmap version"):
            RoadmapImporter(self.store).import_text(
                document(A, B), source="old-import-replay")

    def test_replay_of_old_roadmap_version_cannot_undo_revision(self):
        b2 = dict(B, acceptance=["New B requirement"])
        self.replan.apply(document(A, b2), source="new")
        preview = self.replan.preview(document(A, B))
        self.assertIn("STALE_VERSION_REPLAY", preview.blocked)
        with self.assertRaisesRegex(StoreError, "STALE_VERSION_REPLAY"):
            self.replan.apply(document(A, B), source="old")

    def test_previous_work_and_history_persist_after_revision(self):
        self.assertEqual(self.store.claim_next(online=False).mission_id, "a")
        self.store.checkpoint("a", SHA)
        amended = document(A, dict(B, priority=15), C)
        change = self.replan.apply(amended, source="prioritized-followup")
        self.assertEqual(change.revised, ("b",))
        tasks = {m.id: m for m in self.store.missions()}
        self.assertEqual(tasks["a"].state, "QA_PENDING")
        self.assertEqual(tasks["a"].candidate_sha, SHA)
        self.assertEqual(tasks["b"].priority, 15)
        self.assertEqual(tasks["c"].dependencies, ("b",))
        self.assertIn("ROADMAP_REPLANNED",
                      {e["event"] for e in self.store.events()})

    def test_new_revision_cannot_drop_foreign_or_legacy_tasks(self):
        from holding.autonomy import Mission
        self.store.add_missions([Mission("legacy", REPO)])
        with self.assertRaisesRegex(StoreError, "LEGACY_UNTRACKED"):
            self.replan.apply(document(A, B, C), source="ambiguous")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_versions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()

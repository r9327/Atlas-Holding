"""Replans preserve all checkpoints; only untouched READY work can change."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
import sqlite3

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

    def test_preview_never_installs_or_changes_database_schema(self):
        from holding.autonomy import Mission
        from holding.state_store import StateStore
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            db = StateStore(Path(folder) / "other.sqlite")
            try:
                before = {row[0] for row in db.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                planner = SafeRoadmapReplanner(db)
                with self.assertRaisesRegex(StoreError, "initial roadmap"):
                    planner.preview(document(A))
                after = {row[0] for row in db.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertEqual(before, after)
            finally:
                db.close()

    def test_incremental_direction_inherits_global_project_goal(self):
        goal='Développer une plateforme durable de recherche et de conception.'
        first=json.dumps({'repo':REPO,'project_goal':goal,'missions':[A,B]})
        self.assertEqual(self.replan.apply(first, source='user-goal').revised, ('a','b'))
        delta=self.replan.append_direction(document(C),source='new-mission')
        self.assertEqual(delta.added,('c',))
        specs=[json.loads(row[0]) for row in self.store.db.execute(
            'SELECT spec_json FROM mission_specs ORDER BY id')]
        self.assertEqual([item['project_goal'] for item in specs],[goal]*3)

    def test_project_goal_cannot_be_rewritten_after_mission_starts(self):
        goal='Construire un outil de recherche.'
        first=json.dumps({'repo':REPO,'project_goal':goal,'missions':[A,B]})
        self.replan.apply(first,source='first-goal')
        self.assertEqual(self.store.claim_next(online=False).mission_id,'a')
        changed=json.dumps({'repo':REPO,'project_goal':'Autre projet',
                            'missions':[A,B]})
        with self.assertRaisesRegex(StoreError,'ALREADY_STARTED'):
            self.replan.apply(changed,source='changed-goal')
        with self.assertRaisesRegex(StoreError,'requires full roadmap replan'):
            self.replan.append_direction(
                json.dumps({'repo':REPO,'project_goal':'Autre projet',
                            'missions':[C]}),source='unsafe-delta')
        spec=json.loads(self.store.db.execute(
            "SELECT spec_json FROM mission_specs WHERE id='a'").fetchone()[0])
        self.assertEqual(spec['project_goal'],goal)

    def test_incremental_preview_never_mutates_queue(self):
        before = self.store.events()
        preview = self.replan.preview_direction(document(C))
        self.assertEqual(preview.added, ("c",))
        self.assertEqual(preview.preserved, ("a", "b"))
        self.assertFalse(preview.blocked)
        self.assertEqual({m.id for m in self.store.missions()}, {"a", "b"})
        self.assertEqual(self.store.events(), before)

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

    def test_prior_contracts_survive_revisions_and_reopen_with_pagination(self):
        first_spec = self.store.db.execute(
            "SELECT spec_json FROM mission_specs WHERE id='b'").fetchone()[0]
        changed = dict(B, acceptance=["Critères révisés en français"])
        second = self.replan.apply(document(A, changed), source="new-criteria")
        second_spec = self.store.db.execute(
            "SELECT spec_json FROM mission_specs WHERE id='b'").fetchone()[0]
        third = self.replan.apply(document(A, dict(changed, priority=4)), source="priority")
        self.assertFalse(self.replan.apply(
            document(A, dict(changed, priority=4)), source="replay").applied)
        db_path = self.store.db.execute("PRAGMA database_list").fetchone()[2]
        with StateStore(Path(db_path)) as reopened:
            history = SafeRoadmapReplanner(reopened)
            newest = history.history("b", limit=1)
            self.assertEqual(newest[0].version, second.version)
            self.assertEqual(newest[0].replaced_by_version, third.version)
            self.assertEqual(newest[0].specification, json.loads(second_spec))
            older = history.history("b", before_version=newest[0].version)
            self.assertEqual(len(older), 1)
            self.assertEqual(older[0].specification, json.loads(first_spec))
            self.assertEqual(older[0].replaced_by_version, second.version)
            self.assertEqual(older[0].repository, REPO)
            self.assertEqual(len(older[0].spec_sha256), 64)
        self.assertEqual(self.replan.history("a"), ())
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM mission_spec_history").fetchone()[0], 2)

    def test_goal_revision_archives_old_goal_without_grant_or_started_rewrite(self):
        def with_goal(goal):
            return json.dumps({"repo": REPO, "project_goal": goal, "missions": [A, B]})
        self.replan.apply(with_goal("Premier objectif"), source="first-goal")
        grants = [tuple(r) for r in self.store.db.execute("SELECT * FROM grants")]
        self.replan.apply(with_goal("Objectif précisé"), source="revised-goal")
        prior = self.replan.history("a")[0]
        self.assertEqual(prior.specification["project_goal"], "Premier objectif")
        self.assertEqual(grants, [tuple(r) for r in self.store.db.execute("SELECT * FROM grants")])
        self.assertEqual(self.store.claim_next(online=False).mission_id, "a")
        before = len(self.replan.history("a"))
        with self.assertRaisesRegex(StoreError, "ALREADY_STARTED"):
            self.replan.apply(with_goal("Réécriture interdite"), source="unsafe")
        self.assertEqual(len(self.replan.history("a")), before)

    def test_failed_archive_rolls_back_contracts_tasks_versions_and_events(self):
        before_specs = [tuple(r) for r in self.store.db.execute("SELECT * FROM mission_specs")]
        before_events = self.store.events()
        self.store.db.execute("""CREATE TEMP TRIGGER fail_archive
            BEFORE INSERT ON mission_spec_history BEGIN
            SELECT RAISE(ABORT, 'archive unavailable'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "archive unavailable"):
            self.replan.apply(document(A, dict(B, priority=3), C), source="failed")
        self.assertEqual([tuple(r) for r in self.store.db.execute("SELECT * FROM mission_specs")],
                         before_specs)
        self.assertEqual(len(self.store.missions()), 2)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_versions").fetchone()[0], 1)
        self.assertEqual(self.store.events(), before_events)
        self.assertEqual(self.replan.history("b"), ())

    def test_history_rejects_corrupt_snapshot_and_is_otherwise_read_only(self):
        self.replan.apply(document(A, dict(B, priority=7)), source="change")
        before = self.store.db.total_changes
        self.replan.history("b")
        self.assertEqual(self.store.db.total_changes, before)
        self.store.db.execute(
            "UPDATE mission_spec_history SET spec_json='{}' WHERE mission_id='b'")
        with self.assertRaisesRegex(StoreError, "Corrupt archived"):
            self.replan.history("b")
        for kwargs in ({"limit": True}, {"limit": 0}, {"limit": 101},
                       {"before_version": False}, {"before_version": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(StoreError):
                self.replan.history("b", **kwargs)

    def test_history_on_old_database_does_not_create_tables(self):
        self.store.db.execute("DROP TABLE mission_spec_history")
        before = self.store.db.total_changes
        self.assertEqual(self.replan.history("b"), ())
        self.assertEqual(self.store.db.total_changes, before)
        self.assertIsNone(self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='mission_spec_history'").fetchone())
        self.replan.apply(document(A, dict(B, priority=2)), source="first-new-revision")
        self.assertEqual(len(self.replan.history("b")), 1)

    def test_new_revision_cannot_drop_foreign_or_legacy_tasks(self):
        from holding.autonomy import Mission
        self.store.add_missions([Mission("legacy", REPO)])
        with self.assertRaisesRegex(StoreError, "LEGACY_UNTRACKED"):
            self.replan.apply(document(A, B, C), source="ambiguous")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_versions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()

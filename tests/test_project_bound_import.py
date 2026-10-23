import json
from pathlib import Path
import tempfile
import unittest

from holding.project_bound_import import (
    BoundImportError, import_eligible_project_missions,
)
from holding.project_registry import ProjectRegistry

SHA = "a" * 40
SOURCE = "b" * 64
PROFILE = {
    "schema": "project-profile/v1",
    "name": "dofus-atlas",
    "repository": "r9327/Dof-Atlas",
    "branch": "feat/atlas-work",
    "checkout_dir": "atlas-worktree",
    "commands": {"test": ["python", "-m", "unittest"]},
    "adapters": ["doctor"],
}


def planner():
    return {
        "schema_version": 1, "repository": PROFILE["repository"],
        "snapshot_sha": SHA, "source_uri": "ROADMAP.md",
        "source_digest": SOURCE,
        "requirements": [
            {"id": "r1", "statement": "Code"},
            {"id": "r2", "statement": "Audit"},
            {"id": "r3", "statement": "Manual release"},
        ],
        "lots": [
            {"id": "code", "title": "Code", "dependencies": [],
             "requirement_ids": ["r1"], "acceptance": ["unit tests pass"],
             "autonomy": "AUTO", "state": "READY",
             "external_evidence_required": False,
             "external_review_pending": False},
            {"id": "audit", "title": "Audit", "dependencies": ["code"],
             "requirement_ids": ["r2"], "acceptance": ["test on Windows"],
             "autonomy": "AUTO", "state": "TRIAGED",
             "external_evidence_required": False,
             "external_review_pending": False},
            {"id": "release", "title": "Release", "dependencies": [],
             "requirement_ids": ["r3"], "acceptance": ["human decision"],
             "autonomy": "REQUIRES_DECISION", "state": "BLOCKED",
             "external_evidence_required": True,
             "external_review_pending": True},
        ],
    }


def missions():
    return {"repo": PROFILE["repository"], "missions": [{
        "id": "code", "operation": "code",
        "acceptance": ["unit tests pass"], "depends_on": [],
    }]}


class Writer:
    def __init__(self):
        self.received = []
    def import_text(self, raw, *, source):
        self.received.append((json.loads(raw), source))
        return type("ImportResult", (), {"version": 1})()


class BoundMissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = ProjectRegistry(Path(self.temp.name) / "registry")
        self.entry = self.registry.register(json.dumps(PROFILE))
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        self.writer = Writer()
        self.inventory = planner()
        self.missions = missions()

    def run_import(self, **override):
        kwargs = dict(
            inventory=json.dumps(self.inventory).encode(),
            mission_manifest=json.dumps(self.missions).encode(),
            expected_profile_digest=self.entry.digest,
            expected_snapshot_sha=SHA,
            expected_source_digest=SOURCE,
            runtime_repository=PROFILE["repository"],
            writer=self.writer, source="approved-roadmap",
        )
        kwargs.update(override)
        return import_eligible_project_missions(self.registry, **kwargs)

    def test_imports_only_ready_autonomous_lots(self):
        result = self.run_import()
        self.assertEqual(result.status, "IMPORTED_READY_ONLY")
        self.assertEqual(result.imported_lot_ids, ("code",))
        self.assertEqual(result.deferred_lot_ids, ("audit", "release"))
        self.assertEqual(len(self.writer.received), 1)
        document, source = self.writer.received[0]
        self.assertEqual([row["id"] for row in document["missions"]], ["code"])
        self.assertEqual(source, f"project:dofus-atlas:sha:{SHA}:approved-roadmap")

    def test_foreign_installed_repo_rejected_before_any_write(self):
        with self.assertRaisesRegex(BoundImportError, "installed engine"):
            self.run_import(runtime_repository="other/repo")
        self.assertEqual(self.writer.received, [])

    def test_foreign_mission_repo_rejected(self):
        self.missions["repo"] = "example/other"
        with self.assertRaisesRegex(BoundImportError, "wrong repository"):
            self.run_import()
        self.assertEqual(self.writer.received, [])

    def test_privileged_and_planner_deferred_missions_rejected(self):
        for ident, operation in (("code", "merge"), ("code", "push"), ("audit", "code"),
                                 ("release", "code")):
            with self.subTest(ident=ident, operation=operation):
                self.missions = missions()
                self.missions["missions"] = [{
                    "id": ident, "operation": operation, "acceptance": ["unit tests pass"],
                }]
                with self.assertRaises(BoundImportError):
                    self.run_import()
                self.assertFalse(self.writer.received)

    def test_wrong_acceptance_or_unauthorized_decision_rejected(self):
        for key, value in (("acceptance", ["different"]),
                           ("product_decision_required", True),
                           ("external_required", True),
                           ("depends_on", ["audit"])):
            with self.subTest(key=key):
                self.missions = missions()
                self.missions["missions"][0][key] = value
                with self.assertRaises(BoundImportError):
                    self.run_import()
                self.assertEqual(self.writer.received, [])

    def test_profile_switch_and_stale_digest_refused(self):
        other = dict(PROFILE, name="python-app", repository="example/app",
                     checkout_dir="python-worktree")
        second = self.registry.register(json.dumps(other))
        self.registry.select(second.profile.name, expected_digest=second.digest)
        with self.assertRaisesRegex(BoundImportError, "selected project"):
            self.run_import()
        self.assertEqual(self.writer.received, [])

    def test_incomplete_or_duplicate_missions_rejected(self):
        self.missions["missions"] = []
        with self.assertRaisesRegex(BoundImportError, "represented"):
            self.run_import()
        self.missions["missions"] = missions()["missions"] * 2
        with self.assertRaises(BoundImportError):
            self.run_import()
        self.assertEqual(self.writer.received, [])

    def test_json_duplicate_fields_rejected(self):
        payload = b'{"repo":"r9327/Dof-Atlas","repo":"other/repo","missions":[]}'
        with self.assertRaisesRegex(BoundImportError, "duplicate"):
            self.run_import(mission_manifest=payload)
        self.assertFalse(self.writer.received)

    def test_wrong_snapshot_sha_blocks_import(self):
        with self.assertRaisesRegex(BoundImportError, "selected project"):
            self.run_import(expected_snapshot_sha="c" * 40)
        self.assertFalse(self.writer.received)

    def test_no_eligible_lots_does_not_invoke_writer(self):
        self.inventory["lots"][0]["state"] = "TRIAGED"
        self.missions["missions"] = []
        result = self.run_import()
        self.assertEqual(result.status, "NO_ELIGIBLE_MISSIONS")
        self.assertEqual(result.imported_lot_ids, ())
        self.assertEqual(self.writer.received, [])

    def test_writer_failure_does_not_claim_import(self):
        class FailedWriter:
            def import_text(self, raw, *, source):
                raise RuntimeError("SQLite unavailable")
        with self.assertRaisesRegex(RuntimeError, "SQLite"):
            self.run_import(writer=FailedWriter())


if __name__ == "__main__":
    unittest.main()

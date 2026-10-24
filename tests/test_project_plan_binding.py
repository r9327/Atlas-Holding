import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from holding.project_plan_binding import (
    ProjectPlanBindingError, bind_selected_project_plan,
)
from holding.project_registry import ProjectRegistry


SHA = "a" * 40
SOURCE = "b" * 64
ATLAS = {
    "schema": "project-profile/v1",
    "name": "dofus-atlas",
    "repository": "r9327/Dof-Atlas",
    "branch": "feat/atlas-work",
    "checkout_dir": "dofus-atlas-worktree",
    "commands": {"test": ["python", "-m", "unittest", "discover"]},
    "adapters": ["doctor"],
}
OTHER = {
    "schema": "project-profile/v1",
    "name": "python-simple",
    "repository": "example/python-project",
    "branch": "feat/python-work",
    "checkout_dir": "python-worktree",
    "commands": {"test": ["python", "-m", "unittest", "discover"]},
    "adapters": [],
}


def plan(repository="r9327/Dof-Atlas"):
    return {
        "schema_version": 1,
        "repository": repository,
        "snapshot_sha": SHA,
        "source_uri": "ROADMAP.md",
        "source_digest": SOURCE,
        "requirements": [
            {"id": "req-1", "statement": "Implement feature"},
            {"id": "req-2", "statement": "Prove compatibility"},
        ],
        "lots": [
            {"id": "lot-1", "title": "Feature", "dependencies": [],
             "requirement_ids": ["req-1"],
             "acceptance": ["test passes"], "autonomy": "AUTO",
             "state": "READY", "external_evidence_required": False,
             "external_review_pending": False},
            {"id": "lot-2", "title": "Integration", "dependencies": ["lot-1"],
             "requirement_ids": ["req-2"],
             "acceptance": ["windows works"], "autonomy": "AUTO_WITH_STANDING_GRANT",
             "state": "TRIAGED", "external_evidence_required": True,
             "external_review_pending": True},
        ],
    }


class SelectedProjectPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = ProjectRegistry(self.root / "config")
        self.entry = self.registry.register(json.dumps(ATLAS))
        self.planfile = self.root / "roadmap.json"
        self.planfile.write_text(json.dumps(plan()), encoding="utf-8")

    def bind(self, **overrides):
        kwargs = {
            "expected_profile_digest": self.entry.digest,
            "expected_snapshot_sha": SHA,
            "expected_source_digest": SOURCE,
        }
        kwargs.update(overrides)
        return bind_selected_project_plan(
            self.registry, self.planfile.read_bytes(), **kwargs,
        )

    def test_explicit_selection_works_and_no_execution_is_claimed(self):
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        result = self.bind()
        self.assertEqual(result.status, "PREFLIGHT_ONLY")
        self.assertEqual(result.repository, "r9327/Dof-Atlas")
        self.assertEqual(result.branch, "feat/atlas-work")
        self.assertEqual(result.initially_ready_lots, ("lot-1",))
        self.assertEqual(result.lot_ids, ("lot-1", "lot-2"))
        self.assertEqual(result.profile_digest, self.entry.digest)
        self.assertFalse((self.root / ".git").exists())

    def test_no_implicit_project_selection(self):
        with self.assertRaisesRegex(ProjectPlanBindingError, "no project"):
            self.bind()

    def test_reject_foreign_repo_after_user_selected_atlas(self):
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        self.planfile.write_text(json.dumps(plan("example/python-project")))
        with self.assertRaisesRegex(ProjectPlanBindingError, "roadmap rejected"):
            self.bind()

    def test_changing_selected_project_after_approval_invalidates_run(self):
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        other = self.registry.register(json.dumps(OTHER))
        self.registry.select("python-simple", expected_digest=other.digest)
        with self.assertRaisesRegex(ProjectPlanBindingError, "changed"):
            self.bind()

    def test_cannot_reuse_digest_after_profile_modified(self):
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        changed = dict(ATLAS, branch="feat/new-work")
        updated = self.registry.register(
            json.dumps(changed), expected_digest=self.entry.digest,
        )
        self.assertNotEqual(updated.digest, self.entry.digest)
        with self.assertRaisesRegex(ProjectPlanBindingError, "changed"):
            self.bind()

    def test_sha_and_source_digest_must_match_trusted_inputs(self):
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        with self.assertRaises(ProjectPlanBindingError):
            self.bind(expected_snapshot_sha="c" * 40)
        with self.assertRaises(ProjectPlanBindingError):
            self.bind(expected_source_digest="d" * 64)

    def test_no_roadmap_writes_and_cli_reports_non_execution(self):
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        before = self.planfile.read_bytes()
        result = subprocess.run(
            [sys.executable, "-m", "holding.project_registry_cli",
             "--root", str(self.root / "config"),
             "verify-plan", str(self.planfile),
             "--expected-profile-digest", self.entry.digest,
             "--snapshot-sha", SHA, "--source-digest", SOURCE],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "PREFLIGHT_ONLY")
        self.assertFalse(report["execution_performed"])
        self.assertEqual(report["ready_lots"], ["lot-1"])
        self.assertEqual(report["repository"], "r9327/Dof-Atlas")
        self.assertEqual(before, self.planfile.read_bytes())

    def test_cli_wrong_repo_blocks_without_creating_jobs(self):
        self.registry.select("dofus-atlas", expected_digest=self.entry.digest)
        self.planfile.write_text(json.dumps(plan("example/python-project")))
        result = subprocess.run(
            [sys.executable, "-m", "holding.project_registry_cli",
             "--root", str(self.root / "config"),
             "verify-plan", str(self.planfile),
             "--expected-profile-digest", self.entry.digest,
             "--snapshot-sha", SHA, "--source-digest", SOURCE],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["status"], "BLOCKED")
        self.assertFalse((self.root / "config" / "missions.db").exists())


if __name__ == "__main__":
    unittest.main()

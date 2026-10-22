import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding.project_profile import ProjectProfile
from holding.project_registry import ProjectRegistry, ProjectRegistryError

ATLAS = {
    "schema": "project-profile/v1",
    "name": "dofus-atlas",
    "repository": "r9327/Dof-Atlas",
    "branch": "feat/atlas-work",
    "checkout_dir": "dofus-atlas-worktree",
    "commands": {"test": ["python", "-m", "unittest", "discover"]},
    "adapters": ["doctor", "graphify"],
}
PYTHON = {
    "schema": "project-profile/v1",
    "name": "python-simple",
    "repository": "example/python",
    "branch": "feat/python-work",
    "checkout_dir": "python-worktree",
    "commands": {"test": ["python", "-m", "unittest", "discover"]},
    "adapters": [],
}


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "registry"
        self.registry = ProjectRegistry(self.root)

    def register(self, source):
        return self.registry.register(json.dumps(source))

    def test_register_two_projects_and_select_persists_restart(self):
        atlas = self.register(ATLAS)
        generic = self.register(PYTHON)
        self.assertEqual([p.profile.name for p in self.registry.list_entries()],
                         ["dofus-atlas", "python-simple"])
        self.assertIsNone(self.registry.selected())
        selected = self.registry.select("python-simple", expected_digest=generic.digest)
        self.assertTrue(selected.selected)
        again = ProjectRegistry(self.root).selected()
        self.assertEqual(again.profile.repository, "example/python")
        self.assertEqual(atlas.profile.repository, "r9327/Dof-Atlas")
        self.assertEqual(again.profile.adapters, ())

    def test_updates_require_digest_compare_and_swap(self):
        old = self.register(ATLAS)
        modified = dict(ATLAS, branch="feat/other")
        with self.assertRaisesRegex(ProjectRegistryError, "current digest"):
            self.registry.register(json.dumps(modified))
        replaced = self.registry.register(json.dumps(modified), expected_digest=old.digest)
        self.assertNotEqual(replaced.digest, old.digest)
        with self.assertRaisesRegex(ProjectRegistryError, "profile changed"):
            self.registry.select("dofus-atlas", expected_digest=old.digest)

    def test_failed_selection_does_not_replace_previous(self):
        atlas = self.register(ATLAS)
        self.register(PYTHON)
        self.registry.select("dofus-atlas", expected_digest=atlas.digest)
        with self.assertRaises(ProjectRegistryError):
            self.registry.select("python-simple", expected_digest="0" * 64)
        self.assertEqual(self.registry.selected().profile.name, "dofus-atlas")

    def test_duplicate_worktree_refused(self):
        self.register(ATLAS)
        collision = dict(PYTHON, checkout_dir="dofus-atlas-worktree")
        with self.assertRaisesRegex(ProjectRegistryError, "already assigned"):
            self.registry.register(json.dumps(collision))
        self.assertEqual(len(self.registry.list_entries()), 1)

    def test_no_automatic_execute_or_selection(self):
        fake = dict(PYTHON, commands={"build": ["totally-nonexistent-binary", "anything"]})
        entry = self.register(fake)
        self.assertIsNone(self.registry.selected())
        self.assertEqual(dict(entry.profile.commands)["build"][0], "totally-nonexistent-binary")

    def test_corrupted_registry_is_not_recreated(self):
        self.register(ATLAS)
        file = self.root / "project-registry.json"
        file.write_text('{"broken":"state"}', encoding="utf-8")
        with self.assertRaises(ProjectRegistryError):
            self.registry.list_entries()
        self.assertEqual(file.read_text(), '{"broken":"state"}')

    def test_tampered_hash_rejected(self):
        self.register(ATLAS)
        file = self.root / "project-registry.json"
        state = json.loads(file.read_text())
        state["profiles"]["dofus-atlas"]["digest"] = "e" * 64
        file.write_text(json.dumps(state))
        with self.assertRaisesRegex(ProjectRegistryError, "digest mismatch"):
            ProjectRegistry(self.root).list_entries()

    def test_failed_atomic_write_preserves_original(self):
        self.register(ATLAS)
        file = self.root / "project-registry.json"
        original = file.read_bytes()
        with patch("holding.project_registry.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.register(PYTHON)
        self.assertEqual(file.read_bytes(), original)
        self.assertEqual(len(self.registry.list_entries()), 1)

    def test_clear_selected_is_explicit_and_persistent(self):
        entry = self.register(PYTHON)
        self.registry.select("python-simple", expected_digest=entry.digest)
        self.registry.clear_selection()
        self.assertIsNone(ProjectRegistry(self.root).selected())

    def test_never_run_commands_or_git(self):
        profile = self.register(ATLAS)
        self.assertIsInstance(profile.profile, ProjectProfile)
        self.assertFalse((self.root / ".git").exists())


if __name__ == "__main__":
    unittest.main()

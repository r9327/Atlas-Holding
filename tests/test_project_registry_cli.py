import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ProjectRegistryCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "profiles"
        self.fixture = Path("examples/profiles/python-simple.json")
        self.atlas = Path("examples/profiles/dofus-atlas.json")

    def cli(self, action, *args):
        run = subprocess.run(
            [sys.executable, "-m", "holding.project_registry_cli",
             "--root", str(self.root), action, *map(str, args)],
            capture_output=True, text=True, check=False,
        )
        return run

    def test_register_select_list_and_clear_across_subprocesses(self):
        a = self.cli("register", self.fixture)
        self.assertEqual(a.returncode, 0, a.stderr)
        entry = json.loads(a.stdout)
        self.assertEqual(entry["status"], "REGISTERED")
        self.assertFalse(entry["selected"])
        b = self.cli("register", self.atlas)
        self.assertEqual(b.returncode, 0, b.stderr)
        before = json.loads(self.cli("list").stdout)
        self.assertEqual(len(before["profiles"]), 2)
        selected = self.cli("select", entry["name"], "--expected-digest", entry["digest"])
        self.assertEqual(selected.returncode, 0, selected.stderr)
        after = json.loads(self.cli("list").stdout)
        self.assertEqual([p["name"] for p in after["profiles"] if p["selected"]],
                         ["python-simple"])
        cleared = self.cli("clear")
        self.assertEqual(cleared.returncode, 0, cleared.stderr)
        self.assertFalse(any(x["selected"] for x in json.loads(self.cli("list").stdout)["profiles"]))

    def test_refuse_stale_selection_before_mutating(self):
        entry = json.loads(self.cli("register", self.fixture).stdout)
        wrong = self.cli("select", entry["name"], "--expected-digest", "f" * 64)
        self.assertEqual(wrong.returncode, 2)
        self.assertEqual(json.loads(wrong.stderr)["status"], "BLOCKED")
        self.assertFalse(any(x["selected"] for x in json.loads(self.cli("list").stdout)["profiles"]))

    def test_replace_requires_current_digest(self):
        entry = json.loads(self.cli("register", self.fixture).stdout)
        second = self.cli("register", self.fixture)
        self.assertEqual(second.returncode, 2)
        update = self.cli("register", self.fixture, "--expected-digest", entry["digest"])
        self.assertEqual(update.returncode, 0, update.stderr)

    def test_missing_input_does_not_initialize_registry(self):
        run = self.cli("register", Path(self.tmp.name) / "missing.json")
        self.assertEqual(run.returncode, 2)
        self.assertFalse((self.root / "project-registry.json").exists())


if __name__ == "__main__":
    unittest.main()

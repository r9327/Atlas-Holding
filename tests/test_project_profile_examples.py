import json
from pathlib import Path
import subprocess
import sys
import unittest

from holding.project_profile import load_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = PROJECT_ROOT / "examples" / "profiles"


class ProjectProfileExamplesTests(unittest.TestCase):
    def test_both_real_examples_pass_validator_and_cli(self):
        for filename, expected in (
            ("dofus-atlas.json", "r9327/Dof-Atlas"),
            ("python-simple.json", "example/python-project"),
        ):
            with self.subTest(filename=filename):
                path = EXAMPLES / filename
                profile = load_profile(path.read_text(encoding="utf-8"))
                self.assertEqual(profile.repository, expected)
                self.assertNotIn(profile.branch.lower(), {"main", "master"})
                run = subprocess.run(
                    [sys.executable, "-m", "holding.project_profile_cli", str(path), "--json"],
                    capture_output=True, text=True, check=False, cwd=PROJECT_ROOT,
                )
                self.assertEqual(run.returncode, 0, run.stderr)
                data = json.loads(run.stdout)
                self.assertEqual(data["status"], "VALID")
                self.assertEqual(data["sha256"], profile.digest)

    def test_dofus_is_not_required_by_generic_profile(self):
        python = load_profile((EXAMPLES / "python-simple.json").read_text(encoding="utf-8"))
        self.assertEqual(python.adapters, ())
        self.assertTrue(dict(python.commands).get("test"))


if __name__ == "__main__":
    unittest.main()

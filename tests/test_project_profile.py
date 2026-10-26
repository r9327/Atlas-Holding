import copy
import json
import unittest

from holding.project_profile import InvalidProjectProfile, ProjectProfile, load_profile


BASE = {
    "schema": "project-profile/v1",
    "name": "example-python",
    "repository": "example/python-project",
    "branch": "feature/candidate-1",
    "checkout_dir": "project-worktree",
    "commands": {"test": ["python", "-m", "unittest", "discover", "-s", "tests"]},
    "adapters": ["doctor", "graphify"],
}


class ProjectProfileTests(unittest.TestCase):
    def test_roundtrip_canonical_and_digest(self):
        profile = load_profile(json.dumps(BASE))
        self.assertEqual(profile.repository, BASE["repository"])
        self.assertEqual(profile.digest, ProjectProfile.parse(profile.canonical_dict()).digest)
        clone = copy.deepcopy(BASE)
        clone["adapters"] = ["graphify", "doctor"]
        self.assertEqual(profile.digest, ProjectProfile.parse(clone).digest)

    def test_refuse_bad_schema_and_extra_field(self):
        for change in ({"schema": "other"}, {"surprise": 1}):
            with self.subTest(change=change):
                with self.assertRaises(InvalidProjectProfile):
                    ProjectProfile.parse({**BASE, **change})

    def test_refuse_unsafe_branches(self):
        for branch in ("main", "master", "../../x", "a//b", "a.lock", "a@{b", "bad\\ref"):
            with self.subTest(branch=branch):
                with self.assertRaises(InvalidProjectProfile):
                    ProjectProfile.parse({**BASE, "branch": branch})

    def test_refuse_directory_traversal_and_bad_repository(self):
        for path in ("../other", "C:\\temp", "folder/other", "..", "_bad"):
            with self.subTest(path=path):
                with self.assertRaises(InvalidProjectProfile):
                    ProjectProfile.parse({**BASE, "checkout_dir": path})
        with self.assertRaises(InvalidProjectProfile):
            ProjectProfile.parse({**BASE, "repository": "../../other"})

    def test_refuse_command_shell_string_and_control_characters(self):
        for command in ("python -m unittest", ["python", "-c", "print(1)\nshutdown"], []):
            with self.subTest(command=command):
                with self.assertRaises(InvalidProjectProfile):
                    ProjectProfile.parse({**BASE, "commands": {"test": command}})

    def test_refuse_unknown_or_repeated_adapter(self):
        for adapters in (["other"], ["doctor", "doctor"]):
            with self.assertRaises(InvalidProjectProfile):
                ProjectProfile.parse({**BASE, "adapters": adapters})

    def test_refuse_too_large_or_invalid_json(self):
        for data in ("not json", "{" * 70_000):
            with self.assertRaises(InvalidProjectProfile):
                load_profile(data)

    def test_parse_never_executes_command(self):
        profile = ProjectProfile.parse({**BASE, "commands": {"build": ["this-does-not-exist", "--flag"]}})
        self.assertEqual(dict(profile.commands)["build"][0], "this-does-not-exist")


if __name__ == "__main__":
    unittest.main()

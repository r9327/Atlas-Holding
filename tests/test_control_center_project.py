import unittest
from types import SimpleNamespace

from holding.control_center_project import validate_project_status

SHA = "a" * 40
OTHER = "b" * 40


def state(**kwargs):
    data = dict(repository="r9327/Atlas-Holding", head=SHA,
                remote_head=SHA, ahead=0, behind=0,
                remote_checked=True, dirty=False)
    data.update(kwargs)
    return SimpleNamespace(**data)


def display(value, **kwargs):
    return validate_project_status(project="Holding",
                                   repository="r9327/Atlas-Holding",
                                   git_status=value, eligible_missions=3, **kwargs)


class ProjectStatusTests(unittest.TestCase):
    def test_equal_shas(self):
        self.assertIn("Git : à jour", display(state()))

    def test_dirty_local_status(self):
        self.assertIn("local seulement · modifications locales",
                      display(state(remote_checked=False, dirty=True)))

    def test_behind_and_diverged(self):
        self.assertIn("en retard (-2)", display(state(remote_head=OTHER, behind=2)))
        self.assertIn("divergence (+1/-2)", display(state(remote_head=OTHER, ahead=1, behind=2)))

    def test_remote_missing(self):
        self.assertIn("branche distante absente",
                      display(state(remote_head=None, ahead=None, behind=None)))

    def test_foreign_repo_rejected(self):
        with self.assertRaises(ValueError):
            display(state(repository="other/project"))

    def test_inconsistent_equal_shas_rejected(self):
        with self.assertRaises(ValueError):
            display(state(remote_head=OTHER))

    def test_invalid_counts_rejected(self):
        with self.assertRaises(ValueError):
            display(state(behind=True))

    def test_unverified(self):
        self.assertIn("non vérifié", display(None))


if __name__ == "__main__":
    unittest.main()

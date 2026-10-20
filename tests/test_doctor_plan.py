"""Plan-only Doctor integration against a disposable real Git worktree."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from holding.doctor_plan import DoctorPlanner, DoctorPlanError
from holding.git_worktree import GitWorktrees

REPO = 'r9327/Dof-Atlas'


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


class DoctorPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.canonical = self.root / 'canonical'
        self.canonical.mkdir()
        git(self.canonical, 'init', '-b', 'main')
        git(self.canonical, 'config', 'user.email', 'test@example.invalid')
        git(self.canonical, 'config', 'user.name', 'Test')
        git(self.canonical, 'remote', 'add', 'origin',
            'https://github.com/r9327/Dof-Atlas.git')
        (self.canonical / 'tools').mkdir()
        self.doctor_file = self.canonical / 'tools' / 'atlas_doctor.py'
        self._script(mode='normal')
        self.git = GitWorktrees(self.canonical, self.root / 'worktrees', REPO)
        self.planner = DoctorPlanner(self.git, Path(sys.executable).resolve())

    def _script(self, mode):
        source = (
            "import json,sys,pathlib\n"
            "args=sys.argv[1:]\n"
            "assert '--run-tests' not in args and '--run' not in args\n"
            "assert '--install' not in args\n"
            "candidate=args[args.index('--expected-sha')+1]\n"
            "report={'candidate_sha':candidate,'status':'READY_TO_RUN_SCOPED',"
            "'execution':{'status':'NOT_RUN'},'phase_certified':False}\n"
        )
        if mode == 'forged':
            source += "report['phase_certified']=True\n"
        if mode == 'mutating':
            source += "pathlib.Path('unexpected.py').write_text('modified')\n"
        if mode == 'wrong_sha':
            source += "report['candidate_sha']='b'*40\n"
        source += "print(json.dumps(report))\n"
        self.doctor_file.write_text(source)
        git(self.canonical, 'add', 'tools/atlas_doctor.py')
        git(self.canonical, 'commit', '-m', 'install test Doctor CLI')

    def _create(self):
        work = self.git.create('sample')
        return work.base_sha

    def test_plan_is_advisory_and_bound_to_commit_sha(self):
        sha = self._create()
        advice = self.planner.plan('sample', sha)
        self.assertEqual(advice.candidate_sha, sha)
        self.assertEqual(advice.status, 'READY_TO_RUN_SCOPED')
        self.assertTrue(advice.advisory_only)
        self.assertEqual(len(advice.report_sha256), 64)

    def test_different_sha_refused(self):
        self._create()
        with self.assertRaises(DoctorPlanError):
            self.planner.plan('sample', 'a' * 40)

    def test_doctor_forged_pass_cannot_be_promoted(self):
        self._script('forged')
        sha = self._create()
        with self.assertRaisesRegex(DoctorPlanError, 'certification claim'):
            self.planner.plan('sample', sha)

    def test_doctor_cannot_modify_worktree_and_claim_plan(self):
        self._script('mutating')
        sha = self._create()
        with self.assertRaisesRegex(DoctorPlanError, 'modified the worktree'):
            self.planner.plan('sample', sha)

    def test_changed_doctor_source_requires_independent_review(self):
        sha = self._create()
        work = self.git.inspect('sample')
        doctor = work.path / 'tools' / 'atlas_doctor.py'
        doctor.write_text(doctor.read_text() + "# changed trusted planner\n")
        git(work.path, 'add', 'tools/atlas_doctor.py')
        git(work.path, 'commit', '-m', 'tamper: modify Doctor')
        new_sha = git(work.path, 'rev-parse', 'HEAD')
        self.assertNotEqual(new_sha, sha)
        with self.assertRaisesRegex(DoctorPlanError, 'Doctor.*changed'):
            self.planner.plan('sample', new_sha)

    def test_incorrect_report_sha_refused(self):
        self._script('wrong_sha')
        sha = self._create()
        with self.assertRaisesRegex(DoctorPlanError, 'different SHA'):
            self.planner.plan('sample', sha)


if __name__ == '__main__':
    unittest.main()

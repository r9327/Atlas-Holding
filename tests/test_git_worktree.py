from pathlib import Path
import subprocess
import tempfile
import unittest
from holding.git_worktree import GitWorktrees, GitSafetyError

REPO='r9327/Dof-Atlas'

def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True).stdout.strip()

class GitWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'repo';self.root.mkdir()
        git(self.root,'init','-b','main')
        git(self.root,'config','user.name','Test Runner')
        git(self.root,'config','user.email','runner@example.invalid')
        (self.root/'README.md').write_text('initial\n')
        git(self.root,'add','README.md');git(self.root,'commit','-m','init')
        git(self.root,'remote','add','origin','https://github.com/r9327/Dof-Atlas.git')
        self.manager=GitWorktrees(self.root,Path(self.tmp.name)/'trees',REPO)
    def test_create_commit_without_writing_main(self):
        original=git(self.root,'rev-parse','HEAD')
        work=self.manager.create('feat1')
        self.assertEqual(work.branch,'holding/feat1')
        self.assertEqual(git(self.root,'branch','--show-current'),'main')
        (work.path/'feature.py').write_text('A=1\n')
        sha=self.manager.commit('feat1',('feature.py',),'feat: add code')
        self.assertNotEqual(sha,original)
        self.assertEqual(git(self.root,'rev-parse','HEAD'),original)
        self.assertFalse((self.root/'feature.py').exists())
    def test_dirty_canonical_denied(self):
        (self.root/'NOTTRACKED').write_text('dirty')
        with self.assertRaises(GitSafetyError):self.manager.create('feat1')
    def test_wrong_origin_denied(self):
        git(self.root,'remote','set-url','origin','https://github.com/somebody/else.git')
        with self.assertRaises(GitSafetyError):self.manager.create('feat1')
    def test_branch_reuse_denied(self):
        self.manager.create('feat1')
        with self.assertRaises(GitSafetyError):self.manager.create('feat1')
    def test_unsafe_mission_denied(self):
        with self.assertRaises(GitSafetyError):self.manager.create('../danger')
    def test_secrets_and_workflows_denied(self):
        work=self.manager.create('feat1')
        (work.path/'feature.py').write_text('safe')
        for path in ('../outside','.github/workflows/remove.yml','secrets/token.txt','/tmp/absolute'):
            with self.subTest(path=path),self.assertRaises(GitSafetyError):
                self.manager.commit('feat1',(path,),'feat: unsafe')
    def test_recovery_inspects_dirty_writer(self):
        work=self.manager.create('feat1')
        self.assertTrue(self.manager.inspect_recovery('feat1'))
        (work.path/'unsaved.py').write_text('do not delete')
        self.assertFalse(self.manager.inspect_recovery('feat1'))
        self.assertTrue((work.path/'unsaved.py').exists())
    def test_isolation_enforced(self):
        with self.assertRaises(GitSafetyError):GitWorktrees(self.root,self.root/'holding',REPO)

if __name__=='__main__':unittest.main()

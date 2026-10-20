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
    def test_real_git_nul_delimited_staging_and_commit(self):
        original = git(self.root, 'rev-parse', 'HEAD')
        work = self.manager.create('feature1')
        (work.path / 'one.py').write_text('one = 1\n')
        (work.path / 'two.py').write_text('two = 2\n')
        sha = self.manager.commit('feature1', ('one.py', 'two.py'),
                                  'feat: commit two explicit paths')
        committed = set(git(work.path, 'show', '--pretty=format:',
                            '--name-only', sha).splitlines())
        self.assertEqual(committed, {'one.py', 'two.py'})
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), original)
        self.assertFalse((self.root / 'one.py').exists())

    def test_git_commit_does_not_execute_user_or_repo_hooks(self):
        # Git hooks are executable code, never authorization from the LLM.
        hook_dir = Path(self.tmp.name) / 'untrusted-hooks'
        hook_dir.mkdir()
        marker = Path(self.tmp.name) / 'hook-ran'
        hook = hook_dir / 'pre-commit'
        hook.write_text('#!/bin/sh\necho unsafe > "' + marker.as_posix() +
                        '"\nexit 97\n', encoding='utf-8')
        hook.chmod(0o755)
        git(self.root, 'config', 'core.hooksPath', str(hook_dir))
        git(self.root, 'config', 'commit.gpgsign', 'true')
        tree = self.manager.create('safehook')
        (tree.path / 'feature.py').write_text('safe = True\n')
        sha = self.manager.commit('safehook', ('feature.py',), 'feat: protected commit')
        self.assertEqual(git(tree.path, 'rev-parse', 'HEAD'), sha)
        self.assertFalse(marker.exists(), 'Untrusted pre-commit hook executed')

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
    def test_prestaged_protected_workflow_never_enters_commit(self):
        tree = self.manager.create('feat1')
        protected = tree.path / '.github' / 'workflows' / 'ci.yml'
        protected.parent.mkdir(parents=True)
        protected.write_text('name: CI\\n')
        git(tree.path, 'add', '.github/workflows/ci.yml')
        (tree.path / 'feature.py').write_text('ok=1\\n')
        with self.assertRaises(GitSafetyError):
            self.manager.commit('feat1', ('feature.py',), 'feat: safe change')
        self.assertEqual(git(tree.path, 'branch', '--show-current'), 'holding/feat1')
        self.assertFalse((self.root / 'feature.py').exists())

    def test_wildcard_and_directory_pathspecs_refused(self):
        tree = self.manager.create('feat1')
        (tree.path / 'feature.py').write_text('a=1')
        (tree.path / 'code').mkdir()
        (tree.path / 'code' / 'file.py').write_text('pass')
        for path in ('*', '.', 'code', 'featur?.py', ':(top)feature.py'):
            with self.subTest(path=path), self.assertRaises(GitSafetyError):
                self.manager.commit('feat1', (path,), 'feat: disallow broad staging')

    def test_outside_symlink_refused(self):
        tree = self.manager.create('feat1')
        outside = Path(self.tmp.name) / 'outside'
        outside.write_text('private')
        link = tree.path / 'linked.py'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks not permitted on host')
        with self.assertRaises(GitSafetyError):
            self.manager.commit('feat1', ('linked.py',), 'feat: refuse external link')

    def test_recovery_inspects_dirty_writer(self):
        work=self.manager.create('feat1')
        self.assertTrue(self.manager.inspect_recovery('feat1'))
        (work.path/'unsaved.py').write_text('do not delete')
        self.assertFalse(self.manager.inspect_recovery('feat1'))
        self.assertTrue((work.path/'unsaved.py').exists())
    def test_isolation_enforced(self):
        with self.assertRaises(GitSafetyError):GitWorktrees(self.root,self.root/'holding',REPO)

if __name__=='__main__':unittest.main()

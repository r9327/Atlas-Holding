import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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
    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Objects')
    def test_fast_git_commands_never_use_racy_running_process_attach(self):
        from holding.windows_job import WindowsJob
        # All Git calls are CREATE_SUSPENDED and attached before execution.
        # The legacy attach() must never be used, including for fast queries.
        with patch.object(WindowsJob, 'attach',
                          side_effect=AssertionError('unsafe legacy Git attach')):
            for _ in range(12):
                self.assertEqual(
                    Path(self.manager._git('rev-parse', '--show-toplevel')).resolve(),
                    self.root.resolve(),
                )

    @unittest.skipUnless(os.name == 'nt', 'Requires real Windows Job Objects')
    def test_failed_job_assignment_cleans_up_unassigned_suspended_git(self):
        from holding.windows_job import WindowsJob, WindowsJobError
        started = []

        def deny_before_attach(_job, process):
            started.append(process)
            raise WindowsJobError('Test refuses assignment', code='TEST_DENIED')

        with patch.object(WindowsJob, 'attach_suspended', deny_before_attach):
            with self.assertRaises(WindowsJobError):
                self.manager._git('rev-parse', '--verify', 'HEAD')
        self.assertEqual(len(started), 1)
        self.assertIsNotNone(started[0].poll(),
                             'Suspended Git escaped cleanup after rejected Job')

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

    def test_git_env_config_injection_is_not_inherited(self):
        self.assertEqual(self.manager._git('config', '--get', 'user.name'),
                         'Test Runner')
        with patch.dict(os.environ, {
            'GIT_CONFIG_COUNT': '1',
            'GIT_CONFIG_KEY_0': 'user.name',
            'GIT_CONFIG_VALUE_0': 'Injected Model',
            'GIT_TERMINAL_PROMPT': '1',
        }):
            self.assertEqual(self.manager._git('config', '--get', 'user.name'),
                             'Test Runner')

    def test_git_process_timeout_kills_descendants(self):
        if os.name == 'nt':
            self.skipTest('POSIX test uses a fake Git wrapper')
        fake = Path(self.tmp.name) / 'fake-bin'
        fake.mkdir()
        marker = Path(self.tmp.name) / 'started'
        binary = fake / 'git'
        binary.write_text('#!/bin/sh\n'
                          'echo running > "' + marker.as_posix() + '"\n'
                          'sleep 10\n', encoding='utf-8')
        binary.chmod(0o755)
        import time
        start = time.monotonic()
        with patch.dict(os.environ, {'PATH': str(fake) + os.pathsep + os.environ['PATH']}):
            fake_manager = GitWorktrees(
                self.root, Path(self.tmp.name) / 'fake-trees', REPO,
                git_executable=binary)
            with self.assertRaisesRegex(GitSafetyError, 'timed out'):
                fake_manager._git('status', timeout=1)
        self.assertTrue(marker.exists())
        self.assertLess(time.monotonic() - start, 4)

    def test_git_output_limit_terminates_noisy_helpers(self):
        if os.name == 'nt':
            self.skipTest('POSIX test uses a fake Git wrapper')
        fake = Path(self.tmp.name) / 'noisy-bin'
        fake.mkdir()
        binary = fake / 'git'
        binary.write_text(
            '#!/bin/sh\n'
            'python3 -c "import sys;sys.stdout.write(chr(88)*5000000);sys.stdout.flush()"\n'
            'sleep 10\n', encoding='utf-8')
        binary.chmod(0o755)
        import time
        begin = time.monotonic()
        with patch.dict(os.environ, {'PATH': str(fake) + os.pathsep + os.environ['PATH']}):
            fake_manager = GitWorktrees(
                self.root, Path(self.tmp.name) / 'noisy-trees', REPO,
                git_executable=binary)
            with self.assertRaisesRegex(GitSafetyError, 'output exceeded'):
                fake_manager._git('status', timeout=7)
        self.assertLess(time.monotonic() - begin, 5)

    def test_pinned_git_binary_ignores_later_path_hijack(self):
        fake = Path(self.tmp.name) / 'hijack-bin'
        fake.mkdir()
        binary = fake / 'git'
        binary.write_text('#!/bin/sh\necho malicious\n', encoding='utf-8')
        binary.chmod(0o755)
        self.assertTrue(self.manager.git_executable.is_absolute())
        with patch.dict(os.environ, {'PATH': str(fake) + os.pathsep + os.environ['PATH']}):
            self.assertEqual(self.manager._git('config', '--get', 'user.name'),
                             'Test Runner')

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

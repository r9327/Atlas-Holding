"""Daemon startup does not rerun a confirmed Git commit after process restart."""
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from holding.autonomy import Mission, StandingGrant
from holding.daemon_lease import DaemonLease
from holding.executor import AuthorizedGit
from holding.git_worktree import GitWorktrees
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO='r9327/Dof-Atlas'

def git(directory,*args):
    return subprocess.run(['git','-C',str(directory),*args],capture_output=True,
                          text=True,check=True).stdout.strip()

class Adapter:
    def ready(self): return True
    def __call__(self,mission): raise AssertionError('Interrupted work must not run again')

class StartupRecoveryTests(unittest.TestCase):
    def test_verified_commit_is_queued_for_qa_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);checkout=root/'source';checkout.mkdir()
            git(checkout,'init','-b','main')
            git(checkout,'config','user.email','test@example.invalid')
            git(checkout,'config','user.name','Test')
            (checkout/'README.md').write_text('hello')
            git(checkout,'add','README.md');git(checkout,'commit','-m','initial')
            git(checkout,'remote','add','origin','https://github.com/r9327/Dof-Atlas.git')
            database=root/'holding.sqlite'
            with StateStore(database) as store:
                store.add_missions([Mission('job',REPO)])
                store.grant_for_onboarding(StandingGrant(REPO,frozenset({'code','commit'}),int(time.time())+500))
                manager=GitWorktrees(checkout,root/'worktrees',REPO)
                with DaemonLease(database,heartbeat=False) as lease:
                    self.assertEqual(store.claim_next(online=False,lease_owner=lease.owner).mission_id,'job')
                    actor=AuthorizedGit(store,manager,lease)
                    work=actor.create('job')
                    (work.path/'code.py').write_text('print(123)')
                    commit=actor.commit('job',('code.py',),'feat: file')
            with HoldingService(ServiceSettings(database,idle_seconds=.05),Adapter(),
                                online=lambda:False,recovery_git=manager) as host:
                self.assertEqual(host.start(),('job',))
                self.assertEqual(host.last_recovery[0].state,'QA_PENDING')
                self.assertEqual(host.engine.store.missions()[0].candidate_sha,commit)
                self.assertEqual(host.engine.store.missions()[0].evidence_sha,'')
                self.assertEqual(host.run(max_ticks=1).action,'WAIT')
                self.assertEqual(git(work.path,'rev-parse','HEAD'),commit)

if __name__=='__main__':unittest.main()

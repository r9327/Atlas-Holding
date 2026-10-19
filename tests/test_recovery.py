import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from holding.autonomy import Mission, StandingGrant
from holding.daemon_lease import DaemonLease
from holding.executor import AuthorizedGit
from holding.git_worktree import GitWorktrees
from holding.recovery import RecoveryCoordinator
from holding.state_store import StateStore, StoreError

REPO='r9327/Dof-Atlas'

def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],text=True,
                          capture_output=True,check=True).stdout.strip()

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);canonical=root/'repo';canonical.mkdir()
        git(canonical,'init','-b','main')
        git(canonical,'config','user.name','Test')
        git(canonical,'config','user.email','test@example.invalid')
        (canonical/'README.md').write_text('base')
        git(canonical,'add','README.md');git(canonical,'commit','-m','initial')
        git(canonical,'remote','add','origin','https://github.com/r9327/Dof-Atlas.git')
        self.store=StateStore(root/'state.sqlite');self.addCleanup(self.store.close)
        self.store.add_missions([Mission('x',REPO)])
        self.store.grant_for_onboarding(StandingGrant(REPO,frozenset({'code','commit'}),int(time.time())+120))
        self.lease=DaemonLease(self.store.path,heartbeat=False)
        self.lease.acquire();self.addCleanup(self.lease.release)
        self.git=GitWorktrees(canonical,root/'worktrees',REPO)
        self.actor=AuthorizedGit(self.store,self.git,self.lease)
        self.recovery=RecoveryCoordinator(self.store,self.git,self.lease)
        self.assertEqual(self.store.claim_next(online=False,lease_owner=self.lease.owner).mission_id,'x')

    def quarantine(self):
        self.assertEqual(self.store.quarantine_interrupted(lease_owner=self.lease.owner),('x',))

    def test_applied_commit_moves_only_to_qa_pending(self):
        tree=self.actor.create('x');(tree.path/'code.py').write_text('a=1')
        sha=self.actor.commit('x',('code.py',),'feat: code')
        self.quarantine()
        r=self.recovery.recover('x')
        self.assertEqual(r.state,'QA_PENDING')
        m=self.store.missions()[0]
        self.assertEqual(m.candidate_sha,sha)
        self.assertEqual(m.evidence_sha,'')
        self.assertNotEqual(m.state,'DONE')

    def test_clean_creation_can_resume_without_duplicate(self):
        self.actor.create('x');self.quarantine()
        self.assertEqual(self.recovery.recover('x').state,'READY')
        self.assertEqual(self.store.missions()[0].state,'READY')

    def test_dirty_uncommitted_changes_remain_quarantined(self):
        tree=self.actor.create('x')
        (tree.path/'uncommitted.py').write_text('do not delete')
        self.quarantine()
        self.assertEqual(self.recovery.recover('x').state,'RECOVERY_REQUIRED')
        self.assertTrue((tree.path/'uncommitted.py').exists())

    def test_unknown_job_refused(self):
        self.quarantine()
        with self.assertRaises(StoreError):
            self.recovery.recover('does-not-exist')

    def test_no_journal_does_not_claim_safe_resume(self):
        self.quarantine()
        self.assertEqual(self.recovery.recover('x').state,'RECOVERY_REQUIRED')

if __name__=='__main__': unittest.main()

import tempfile
import time
from pathlib import Path
import subprocess
import unittest
from holding.executor import AuthorizedGit
from holding.daemon_lease import DaemonLease, LeaseError
from holding.git_worktree import GitWorktrees, GitSafetyError
from holding.state_store import StateStore
from holding.autonomy import StandingGrant

REPO='r9327/Dof-Atlas'

def git(root,*args):
    subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True)

class AuthorizedGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'repo';self.root.mkdir()
        git(self.root,'init','-b','main')
        git(self.root,'config','user.email','t@example.invalid')
        git(self.root,'config','user.name','Test')
        (self.root/'README.md').write_text('hi')
        git(self.root,'add','README.md');git(self.root,'commit','-m','init')
        git(self.root,'remote','add','origin','https://github.com/r9327/Dof-Atlas.git')
        self.store=StateStore(Path(self.tmp.name)/'state.sqlite');self.addCleanup(self.store.close)
        self.store.db.execute('''INSERT INTO missions(
            id,repo,operation,dependencies,priority,state,candidate_sha,evidence_sha,
            external_required,product_decision_required,failures_without_progress,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('one',REPO,'code','[]',0,'READY','','',0,0,0,0))
        self.git=GitWorktrees(self.root,Path(self.tmp.name)/'trees',REPO)
        self.lease=DaemonLease(self.store.path, heartbeat=False)
        self.lease.acquire()
        self.addCleanup(self.lease.release)
        self.actor=AuthorizedGit(self.store,self.git,self.lease)
    def grant(self,*ops,expiry=None):
        self.store.grant_for_onboarding(StandingGrant(REPO,frozenset(ops),
            int(time.time())+3600 if expiry is None else expiry))
    def claim(self):
        self.assertEqual(self.store.claim_next(online=False).mission_id,'one')
    def test_stale_lease_cannot_write_git(self):
        self.grant('code')
        self.claim()
        self.lease.release()
        with self.assertRaises(LeaseError):
            self.actor.create('one')

    def test_no_grant_no_dispatch(self):
        with self.assertRaises(GitSafetyError):self.actor.create('one')
    def test_grant_and_claim_required(self):
        self.grant('code')
        with self.assertRaises(GitSafetyError):self.actor.create('one')
        self.claim()
        work=self.actor.create('one')
        self.assertEqual(work.branch,'holding/one')
    def test_expired_grant_refused(self):
        self.grant('code',expiry=int(time.time())-1)
        self.store.db.execute("UPDATE missions SET state='RUNNING' WHERE id='one'")
        with self.assertRaises(GitSafetyError):self.actor.create('one')
    def test_repo_mismatch_denied(self):
        self.grant('code')
        self.store.db.execute("UPDATE missions SET state='RUNNING',repo='r9327/other' WHERE id='one'")
        with self.assertRaises(GitSafetyError):self.actor.create('one')
    def test_commits_need_separate_permission_and_revoke(self):
        self.grant('code');self.claim()
        work=self.actor.create('one')
        (work.path/'code.py').write_text('a=1')
        with self.assertRaises(GitSafetyError):self.actor.commit('one',('code.py',),'feat: code')
        self.grant('commit')
        self.actor.commit('one',('code.py',),'feat: code')
        self.store.revoke(REPO)
        with self.assertRaises(GitSafetyError):self.actor.commit('one',('code.py',),'feat: again')
    def test_pause_refuses_operations(self):
        self.grant('code');self.claim();self.store.set_paused(True)
        with self.assertRaises(GitSafetyError):self.actor.create('one')
    def test_stop_refuses_git_even_if_mission_is_already_claimed(self):
        self.grant('code','commit');self.claim()
        self.store.db.execute(
            "INSERT INTO settings(key,value) VALUES('stop_requested','1')")
        with self.assertRaisesRegex(GitSafetyError,'stopped'):
            self.actor.create('one')

if __name__=='__main__':unittest.main()

import json
import subprocess
import sys
import tempfile
from pathlib import Path
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.code_worker import CodePlanError, LocalCodeWorker
from holding.daemon_lease import DaemonLease
from holding.executor import AuthorizedGit
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram, LocalProcessAgent
from holding.state_store import StateStore

REPO='r9327/Dof-Atlas'

def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],text=True,capture_output=True,check=True).stdout.strip()

class CodeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.root=root
        self.canonical=root/'canonical';self.canonical.mkdir()
        git(self.canonical,'init','-b','main')
        git(self.canonical,'config','user.name','Runner')
        git(self.canonical,'config','user.email','runner@example.invalid')
        (self.canonical/'README.md').write_text('base')
        git(self.canonical,'add','README.md');git(self.canonical,'commit','-m','init')
        git(self.canonical,'remote','add','origin','https://github.com/r9327/Dof-Atlas.git')
        self.store=StateStore(root/'state.db');self.addCleanup(self.store.close)
        self.store.add_missions([Mission('job',REPO)])
        self.store.grant_for_onboarding(StandingGrant(REPO,frozenset({'code','commit'}),int(time.time())+500))
        self.lease=DaemonLease(self.store.path,heartbeat=False);self.lease.acquire();self.addCleanup(self.lease.release)
        self.git=GitWorktrees(self.canonical,root/'trees',REPO)
        self.actor=AuthorizedGit(self.store,self.git,self.lease)
        self.store.claim_next(online=False,lease_owner=self.lease.owner)
        self.agent_path=root/'fake_adapter.py'

    def worker(self, plan, *, direct_mutation=False, ready=True):
        self.agent_path.write_text('''import json,sys
from pathlib import Path
req=json.load(sys.stdin)
if req['action']=='health':
    print(json.dumps({'protocol':1,'action':'health','status':'ok','agent_ready':'''+repr(ready)+'''}))
else:
    '''+('Path("UNAUTHORIZED.py").write_text("modified")' if direct_mutation else 'pass')+'''
    print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok','plan':'''+repr(plan)+'''}))
''')
        worker=LocalProcessAgent(AgentProgram(Path(sys.executable).resolve(),(str(self.agent_path),),timeout_seconds=2),should_stop=lambda:False)
        return LocalCodeWorker(self.actor,worker,health_directory=self.root)

    def plan(self, path='src/feature.py'):
        return {'files':[{'path':path,'content':'answer=42\n'}], 'commit_message':'feat: add verified patch'}

    def test_real_local_agent_creates_exact_git_commit_not_done(self):
        worker=self.worker(self.plan())
        self.assertTrue(worker.ready())
        original=git(self.canonical,'rev-parse','HEAD')
        sha=worker(Mission('job',REPO))
        self.assertNotEqual(sha,original)
        self.assertEqual(git(self.canonical,'rev-parse','HEAD'),original)
        self.assertEqual(self.store.missions()[0].state,'RUNNING')
        self.assertEqual((self.root/'trees'/'job'/'src'/'feature.py').read_text(),'answer=42\n')
        self.assertIn('Holding-Operation:',git(self.root/'trees'/'job','log','-1','--format=%B'))

    def test_protected_security_policy_path_denied(self):
        worker=self.worker(self.plan('.github/workflows/ci.yml'))
        with self.assertRaises(CodePlanError): worker(Mission('job',REPO))
        self.assertEqual(git(self.canonical,'rev-parse','HEAD'),git(self.root/'trees'/'job','rev-parse','HEAD'))

    def test_directory_escape_denied(self):
        worker=self.worker(self.plan('../escape.py'))
        with self.assertRaises(CodePlanError): worker(Mission('job',REPO))
        self.assertFalse((self.root/'escape.py').exists())

    def test_worker_cannot_write_before_supervisor_authorization(self):
        self.store.revoke(REPO)
        worker=self.worker(self.plan())
        with self.assertRaises(Exception):worker(Mission('job',REPO))
        self.assertFalse((self.root/'trees'/'job').exists())

    def test_agent_direct_mutation_denied(self):
        worker=self.worker(self.plan(),direct_mutation=True)
        with self.assertRaisesRegex(CodePlanError,'modified worktree'):worker(Mission('job',REPO))
        self.assertFalse((self.root/'trees'/'job'/'src'/'feature.py').exists())

    def test_no_healthy_worker_makes_no_git_changes(self):
        worker=self.worker(self.plan(),ready=False)
        self.assertFalse(worker.ready())
        with self.assertRaises(Exception):worker(Mission('job',REPO))
        self.assertFalse((self.root/'trees'/'job').exists())

if __name__=='__main__':unittest.main()

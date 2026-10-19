import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from holding.autonomy import Mission, StandingGrant
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO='r9327/Dof-Atlas'

def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True).stdout.strip()

class ServiceWithAgentTests(unittest.TestCase):
    def test_mission_runs_real_process_and_waits_for_independent_qa(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);canonical=root/'canonical';canonical.mkdir()
            git(canonical,'init','-b','main')
            git(canonical,'config','user.email','test@example.invalid')
            git(canonical,'config','user.name','Test')
            (canonical/'README.md').write_text('initial\n')
            git(canonical,'add','README.md');git(canonical,'commit','-m','initial')
            git(canonical,'remote','add','origin','https://github.com/r9327/Dof-Atlas.git')
            script=root/'trusted.py'
            script.write_text('''import json, sys
req=json.load(sys.stdin)
if req['action']=='health':
 print(json.dumps({'protocol':1,'action':'health','status':'ok','agent_ready':True}))
else:
 print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok','plan':{'files':[{'path':'src/generated.py','content':'result=42\\n'}], 'commit_message':'feat: local agent output'}}))
''')
            db=root/'holding.sqlite';root_sha=git(canonical,'rev-parse','HEAD')
            with StateStore(db) as store:
                store.add_missions([Mission('task',REPO)])
                store.grant_for_onboarding(StandingGrant(REPO,frozenset({'code','commit'}),int(time.time())+3600))
            localgit=GitWorktrees(canonical,root/'jobs',REPO)
            program=AgentProgram(Path(sys.executable).resolve(),(str(script),),timeout_seconds=3)
            with HoldingService.from_local_agent(ServiceSettings(db,idle_seconds=.05),program=program,
                    git=localgit,health_directory=root,online=lambda:False) as service:
                self.assertEqual(service.start(),())
                out=service.run(max_ticks=1)
                self.assertEqual(out.action,'QA_PENDING')
                job=service.engine.store.missions()[0]
                self.assertEqual(job.state,'QA_PENDING')
                self.assertEqual(job.evidence_sha,'')
                self.assertEqual(job.candidate_sha,git(root/'jobs'/'task','rev-parse','HEAD'))
                self.assertEqual(git(canonical,'rev-parse','HEAD'),root_sha)
                self.assertEqual((root/'jobs'/'task'/'src'/'generated.py').read_text(),'result=42\n')

if __name__=='__main__':unittest.main()

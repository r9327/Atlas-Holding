"""Old daemon must kill a still-generating model on lease takeover."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from holding.autonomy import StandingGrant
from holding.daemon_lease import DaemonLease
from holding.git_worktree import GitWorktrees
from holding.process_agent import AgentProgram
from holding.roadmap_import import RoadmapImporter
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO='r9327/Dof-Atlas'

def git(path,*args):
    return subprocess.run(['git','-C',str(path),*args],check=True,
                          capture_output=True,text=True).stdout.strip()

class AgentPreemptionTests(unittest.TestCase):
    def test_lost_daemon_lease_kills_generating_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);repo=root/'repo';repo.mkdir()
            git(repo,'init','-b','main');git(repo,'config','user.email','fixture@invalid.test')
            git(repo,'config','user.name','Test')
            (repo/'README.md').write_text('base')
            git(repo,'add','README.md');git(repo,'commit','-m','initial')
            git(repo,'remote','add','origin','https://github.com/r9327/Dof-Atlas.git')
            script=root/'sleep_agent.py'
            script.write_text('''import json,sys,time
req=json.load(sys.stdin)
if req['action']=='health':
 print(json.dumps({'protocol':1,'action':'health','status':'ok','agent_ready':True}))
else:
 time.sleep(15)
 print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok','plan':{'files':[{'path':'evil.py','content':'x=1'}],'commit_message':'feat: after lease loss'}}))
''')
            dbpath=root/'state.sqlite'
            with StateStore(dbpath) as db:
                RoadmapImporter(db).import_text(json.dumps({'repo':REPO,'missions':[
                    {'id':'job','operation':'code','acceptance':['do something']}]}),source='test')
                db.grant_for_onboarding(StandingGrant(REPO,frozenset({'code','commit'}),int(time.time())+3600))
            worktrees=GitWorktrees(repo,root/'work',REPO)
            program=AgentProgram(Path(sys.executable).resolve(),(str(script),),timeout_seconds=20)
            with HoldingService.from_local_agent(ServiceSettings(dbpath,idle_seconds=.05),
                       program=program,git=worktrees,health_directory=root,online=lambda:False) as service:
                service.start()
                replacement = DaemonLease(dbpath,heartbeat=False)
                def takeover():
                    time.sleep(.35)
                    with StateStore(dbpath) as db:
                        with db._transaction():
                            db.db.execute('UPDATE daemon_lease SET expires_epoch=0')
                    replacement.acquire()
                thread=threading.Thread(target=takeover,daemon=True)
                thread.start()
                begin=time.monotonic()
                try:
                    result=service.run(max_ticks=1)
                    self.assertEqual(result.action,'LEASE_LOST')
                    self.assertLess(time.monotonic()-begin,5.0)
                    self.assertFalse((root/'work'/'job'/'evil.py').exists())
                    self.assertEqual(service.engine.store.missions()[0].state,'RUNNING')
                finally:
                    thread.join(timeout=2)
                    replacement.release()

if __name__=='__main__': unittest.main()

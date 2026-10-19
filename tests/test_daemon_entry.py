import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import time
import unittest

from holding.autonomy import Mission, StandingGrant
from holding.daemon_entry import Installation, BootstrapError
from holding.state_store import StateStore
from holding.roadmap_import import RoadmapImporter

REPO='r9327/Dof-Atlas'

def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True).stdout.strip()

class InstallerBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.repo=self.root/'canonical';self.repo.mkdir()
        git(self.repo,'init','-b','main')
        git(self.repo,'config','user.name','Runner')
        git(self.repo,'config','user.email','runner@example.invalid')
        (self.repo/'README.md').write_text('ok')
        git(self.repo,'add','README.md');git(self.repo,'commit','-m','init')
        git(self.repo,'remote','add','origin','https://github.com/r9327/Dof-Atlas.git')
        self.script=self.root/'agent.py'
        self.script.write_text('''import json,sys
r=json.load(sys.stdin)
if r['action']=='health':
 print(json.dumps({'protocol':1,'action':'health','status':'ok','agent_ready':True}))
else:
 print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok','plan':{'files':[{'path':'x.py','content':'x=1'}],'commit_message':'feat: add test'}}))
''')
        self.config=self.root/'install.json'
        self.data={'schema':1,'database':str(self.root/'state.sqlite'),
                  'repository':REPO,'canonical_root':str(self.repo),
                  'workspace':str(self.root/'worktrees'),'health_directory':str(self.root),
                  'executable':str(Path(sys.executable).resolve()),
                  'executable_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                  'arguments':[str(self.script)],'timeout_seconds':3}

    def save(self):self.config.write_text(json.dumps(self.data))

    def test_installed_headless_service_end_to_end(self):
        self.save()
        db=Path(self.data['database'])
        with StateStore(db) as state:
            RoadmapImporter(state).import_text(json.dumps({'repo':REPO,'missions':[{'id':'one','operation':'code','acceptance':['x=1 must exist']}]}),source='test')
            state.grant_for_onboarding(StandingGrant(REPO,frozenset({'code','commit'}),int(time.time())+300))
        with Installation.load(self.config).build_service() as service:
            service.start()
            self.assertEqual(service.run(max_ticks=1).action,'QA_PENDING')
            self.assertTrue((self.root/'worktrees'/'one'/'x.py').is_file())
            self.assertEqual(service.engine.store.missions()[0].evidence_sha,'')

    def test_unknown_config_fields_refused(self):
        self.data['trusted_doctor_override']=True;self.save()
        with self.assertRaises(BootstrapError):Installation.load(self.config)

    def test_wrong_binary_checksum_refused(self):
        self.data['executable_sha256']='0'*64;self.save()
        with self.assertRaises(BootstrapError):Installation.load(self.config)

    def test_relative_paths_refused(self):
        self.data['workspace']='../unsafe';self.save()
        with self.assertRaises(BootstrapError):Installation.load(self.config)

    def test_untrusted_agent_executable_not_allowed(self):
        self.data['executable']=str(self.script);self.save()
        with self.assertRaises(BootstrapError):Installation.load(self.config)

if __name__=='__main__':unittest.main()

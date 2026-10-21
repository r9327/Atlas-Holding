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
from holding.github_broker_protocol import BrokerGitPushControl
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
                  'arguments':[str(self.script)],'argument_sha256':{str(self.script):hashlib.sha256(self.script.read_bytes()).hexdigest()},'timeout_seconds':3}

    def enable_github(self):
        broker = self.root / 'broker.py'
        broker.write_text('''import json,sys
r=json.load(sys.stdin)
json.dump({'schema_version':1,'request_id':r['request_id'],'status':'OK',
           'result':{'account_id':'github:r9327'}},sys.stdout)
''')
        executable = Path(sys.executable).resolve()
        self.data['schema'] = 2
        self.data['github'] = {
            'account_id': 'github:r9327', 'policy_sha': 'a' * 40,
            'broker_command': [str(executable), str(broker)],
            'broker_file_sha256': {
                str(executable): hashlib.sha256(executable.read_bytes()).hexdigest(),
                str(self.script): hashlib.sha256(self.script.read_bytes()).hexdigest(),
                str(broker): hashlib.sha256(broker.read_bytes()).hexdigest(),
            },
            'broker_spool_root': str(self.root),
            'broker_timeout_seconds': 5,
        }
        return broker

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

    def test_agent_script_modified_after_onboarding_refused(self):
        self.save()
        self.script.write_text('print(123)')
        with self.assertRaises(BootstrapError):Installation.load(self.config)

    def test_schema_two_wires_pinned_github_health_and_push_role(self):
        self.enable_github(); self.save()
        installation = Installation.load(self.config)
        self.assertEqual(
            Path(installation.github.broker.command[1]),
            Path(installation.github.broker.command[1]).resolve(strict=True))
        with installation.build_service() as service:
            self.assertEqual(
                service.worker.available_operations(), frozenset({'code', 'push'}))
            push = service.worker._adapters['push']
            self.assertIsInstance(
                push.control_factory('source-1'), BrokerGitPushControl)
            self.assertIsNotNone(service._github_health)
            now = [100]
            service._github_health.clock = lambda: now[0]
            service.start()
            self.assertEqual(service.run(max_ticks=1).action, 'COMPLETE')
            self.assertEqual(
                service.engine.store.service_health('github')['status'], 'PROBING')
            now[0] += 1
            self.assertEqual(service.run(max_ticks=1).action, 'COMPLETE')
            self.assertEqual(
                service.engine.store.service_health('github')['status'], 'ONLINE')

    def test_changed_schema_two_broker_is_refused_during_load(self):
        broker = self.enable_github(); self.save()
        broker.write_text('raise SystemExit(9)')
        with self.assertRaisesRegex(BootstrapError, 'changed since onboarding'):
            Installation.load(self.config)

    def test_schema_two_refuses_partially_pinned_python_package(self):
        self.enable_github()
        del self.data['github']['broker_file_sha256'][str(self.script)]
        self.save()
        with self.assertRaisesRegex(BootstrapError, 'Unsafe privileged broker'):
            Installation.load(self.config)

    def test_schema_two_requires_exact_github_contract(self):
        self.data['schema'] = 2
        self.save()
        with self.assertRaises(BootstrapError):
            Installation.load(self.config)

    def _enable_dashboard(self, *, readonly=False):
        script = self.root / 'viewer.py'
        script.write_text(
            "import pathlib,sys,time\n"
            "data=sys.stdin.buffer.read(65)\n"
            "assert len(data)==(32 if '--readonly' in sys.argv else 64)\n"
            "pathlib.Path(" + repr(str(self.root / 'viewer-started')) +
            ").write_text('authenticated-pipe-delivered')\n"
            "time.sleep(15)\n"
        )
        self.data['schema'] = 3
        self.data['github'] = None
        self.data['dashboard'] = {
            'executable': str(Path(sys.executable).resolve()),
            'executable_sha256':
                hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            'script': str(script),
            'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
            'readonly': readonly,
        }
        return script

    def test_schema_three_launches_real_pinned_viewer(self):
        self._enable_dashboard()
        self.save()
        installation = Installation.load(self.config)
        self.assertIsNone(installation.github)
        self.assertFalse(installation.dashboard.readonly)
        with installation.build_service() as service:
            service.start()
            pid = service.launch_dashboard(installation.dashboard.program)
            self.assertGreater(pid, 0)
            marker = self.root / 'viewer-started'
            for _ in range(100):
                if marker.is_file():
                    break
                time.sleep(.025)
            self.assertEqual(marker.read_text(), 'authenticated-pipe-delivered')
            self.assertIsNotNone(service._dashboard_ipc)
        self.assertIsNone(service._dashboard_ipc)
        self.assertIsNone(service._dashboard_session)

    def test_schema_three_bad_dashboard_pins_fail_before_agent_start(self):
        script = self._enable_dashboard()
        self.save()
        script.write_text('raise RuntimeError("tampered")')
        with self.assertRaises(BootstrapError):
            Installation.load(self.config)
        self.data['dashboard']['script_sha256'] = '0' * 64
        self.save()
        with self.assertRaises(BootstrapError):
            Installation.load(self.config)

    def test_schema_three_denies_unknown_ui_keys_and_malformed_rights(self):
        self._enable_dashboard(readonly=True)
        self.data['dashboard']['source'] = 'roadmap'
        self.save()
        with self.assertRaises(BootstrapError):
            Installation.load(self.config)
        del self.data['dashboard']['source']
        self.data['dashboard']['readonly'] = 1
        self.save()
        with self.assertRaises(BootstrapError):
            Installation.load(self.config)

    def test_schema_three_supports_optional_pinned_github(self):
        script = self._enable_dashboard()
        self.enable_github()
        # The trusted broker also rejects unpinned Python code in its parent
        # package. The viewer harness must be pinned independently in BOTH
        # trust manifests; otherwise the broker correctly fails closed.
        self.data['github']['broker_file_sha256'][str(script)] = (
            hashlib.sha256(script.read_bytes()).hexdigest())
        self.data['schema'] = 3
        self.save()
        installation = Installation.load(self.config)
        self.assertIsNotNone(installation.github)
        self.assertIsNotNone(installation.dashboard)

    def test_unpinned_inline_code_refused(self):
        self.data['arguments']=['-c','print(123)']
        self.data['argument_sha256']={}
        self.save()
        with self.assertRaises(BootstrapError):Installation.load(self.config)

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

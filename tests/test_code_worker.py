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
from holding.roadmap_import import RoadmapImporter

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
        RoadmapImporter(self.store).import_text(json.dumps({
            'repo': REPO,
            'project_goal': 'Créer un assistant de gestion de quêtes respectant les joueurs.',
            'missions': [{'id': 'job', 'operation': 'code',
                          'acceptance': ['feature.py works']}],
        }), source='local-test')
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

    def test_existing_code_hints_reach_agent_before_patch(self):
        (self.canonical / "feature.py").write_text(
            "class ExistingFeature:\n    pass\n", encoding="utf-8")
        git(self.canonical, "add", "feature.py")
        git(self.canonical, "commit", "-m", "Existing implementation")
        self.agent_path.write_text('''import json,sys
req=json.load(sys.stdin)
if req['action']=='health':
 print(json.dumps({'protocol':1,'action':'health','status':'ok','agent_ready':True}))
else:
 evidence=req['payload']['existing_code']
 assert evidence['repository_sha']==req['payload']['base_sha']
 assert evidence['github_pr_status']=='NOT_CHECKED'
 assert evidence['functionality_status']=='NOT_VERIFIED'
 assert evidence['can_skip_implementation'] is False
 assert any(x['path']=='feature.py' and 'ExistingFeature' in x['symbols']
            for x in evidence['candidates'])
 print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok',
 'plan':{'files':[{'path':'safe.py','content':'ok=True\\n'}],
         'commit_message':'feat: reuse indexed module'}}))
''', encoding="utf-8")
        runner=LocalProcessAgent(AgentProgram(Path(sys.executable).resolve(),
                (str(self.agent_path),), timeout_seconds=5))
        worker=LocalCodeWorker(self.actor,runner,health_directory=self.root)
        sha=worker(Mission('job',REPO))
        self.assertEqual(len(sha),40)
        self.assertEqual((self.root/'trees'/'job'/'safe.py').read_text(),
                         'ok=True\n')

    def test_failed_exact_tree_discovery_blocks_model_patch(self):
        from unittest.mock import patch
        from holding.existing_code import ExistingCodeError
        worker=self.worker(self.plan())
        original=worker.agent.request
        calls=[]
        def guarded_request(action,payload,*,cwd):
            calls.append(action)
            return original(action,payload,cwd=cwd)
        worker.agent.request=guarded_request
        with patch("holding.code_worker.inspect_existing_code",
                   side_effect=ExistingCodeError("Git unavailable")):
            with self.assertRaisesRegex(CodePlanError, "existing-code inventory"):
                worker(Mission('job',REPO))
        self.assertEqual(calls, ["health"])
        self.assertFalse((self.root/'trees'/'job'/'src'/'feature.py').exists())

    def test_github_broker_inventory_is_present_before_local_model_patch(self):
        from holding.github_pr_inventory import OpenPrHint, OpenPrInventory

        class Broker:
            calls = []
            def observe_open_prs(self, repo):
                self.calls.append(repo)
                return OpenPrInventory(
                    repo, "OPEN_PRS_OBSERVED", 1, (
                        OpenPrHint(17, "a" * 40, "b" * 40,
                                   "holding/existing-code", True, True),))

        broker = Broker()
        worker = self.worker(self.plan())
        worker.github_pr_observer = broker
        run = worker.agent.request
        observed = []
        def capture(action, payload, *, cwd):
            if action == 'plan_patch':
                observed.append(payload)
            return run(action, payload, cwd=cwd)
        worker.agent.request = capture
        worker(Mission('job', REPO))
        self.assertEqual(broker.calls, [REPO])
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]['github_pr_observation'], 'OBSERVED')
        self.assertEqual(observed[0]['github_open_prs']['hints'][0]['number'], 17)
        self.assertFalse(observed[0]['github_open_prs']['can_skip_implementation'])

    def test_github_broker_failure_never_forges_empty_pr_inventory(self):
        from holding.github_full_evidence import GitHubEvidenceError

        class OfflineBroker:
            def observe_open_prs(self, repository):
                raise GitHubEvidenceError('Remote unavailable')

        worker = self.worker(self.plan())
        worker.github_pr_observer = OfflineBroker()
        run = worker.agent.request
        captured = []
        def capture(action, payload, *, cwd):
            if action == 'plan_patch':
                captured.append(payload)
            return run(action, payload, cwd=cwd)
        worker.agent.request = capture
        worker(Mission('job', REPO))
        self.assertEqual(captured[0]['github_pr_observation'], 'UNAVAILABLE')
        self.assertNotIn('github_open_prs', captured[0])

    def test_one_relevant_pr_path_is_checked_before_model_patch(self):
        from holding.github_pr_inventory import OpenPrHint, OpenPrInventory
        from holding.github_pr_changed_paths import PrChangedPaths

        (self.canonical / "feature.py").write_text(
            "class ExistingFeature:\n    pass\n", encoding="utf-8")
        git(self.canonical, "add", "feature.py")
        git(self.canonical, "commit", "-m", "already exists")

        class Broker:
            calls = []
            def observe_open_prs(self, repository):
                self.calls.append("list")
                return OpenPrInventory(
                    repository, "OPEN_PRS_OBSERVED", 1,
                    (OpenPrHint(17, "a"*40, "b"*40, "fix/works",
                                False, True),))
            def observe_pr_paths(self, repository, number, sha):
                self.calls.append(("paths", number, sha))
                return PrChangedPaths(
                    repository, number, sha, "PR_PATHS_BOUNDED", 1,
                    ("feature.py",))

        broker = Broker()
        worker = self.worker(self.plan())
        worker.github_pr_observer = broker
        original = worker.agent.request
        observed = []
        def capture(action, payload, *, cwd):
            if action == "plan_patch":
                observed.append(payload)
            return original(action, payload, cwd=cwd)
        worker.agent.request = capture
        worker(Mission("job", REPO))
        self.assertEqual(broker.calls, [
            "list", ("paths", 17, "a"*40)])
        self.assertEqual(observed[0]["github_pr_path_status"], "OBSERVED")
        collision = observed[0]["github_pr_path_overlap"]
        self.assertEqual(tuple(collision["matching_local_paths"]), ("feature.py",))
        self.assertEqual(collision["status"], "EXACT_PATH_OVERLAP")
        self.assertFalse(collision["can_skip_implementation"])

    def test_irrelevant_pr_branch_does_not_fetch_changed_files(self):
        from holding.github_pr_inventory import OpenPrHint, OpenPrInventory
        class Broker:
            calls = []
            def observe_open_prs(self, repository):
                self.calls.append("list")
                return OpenPrInventory(
                    repository, "OPEN_PRS_OBSERVED", 1,
                    (OpenPrHint(18, "a"*40, "b"*40,
                                "chore/unrelated", False, True),))
            def observe_pr_paths(self, *args):
                raise AssertionError("Unrelated PR file read")

        broker = Broker()
        worker = self.worker(self.plan())
        worker.github_pr_observer = broker
        original = worker.agent.request
        observed = []
        def capture(action, payload, *, cwd):
            if action == "plan_patch":
                observed.append(payload)
            return original(action, payload, cwd=cwd)
        worker.agent.request = capture
        worker(Mission("job", REPO))
        self.assertEqual(broker.calls, ["list"])
        self.assertEqual(observed[0]["github_pr_path_status"], "NOT_CHECKED")
        self.assertNotIn("github_pr_path_overlap", observed[0])

    def test_original_roadmap_acceptance_reaches_local_agent(self):
        self.agent_path.write_text('''import json,sys
req=json.load(sys.stdin)
if req['action']=='health':
 print(json.dumps({'protocol':1,'action':'health','status':'ok','agent_ready':True}))
else:
 assertion=req['payload']['acceptance'][0]
 print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok',
 'plan':{'files':[{'path':'goal.py','content':repr(assertion)}],
         'commit_message':'feat: roadmap grounded code'}}))
''')
        runner = LocalProcessAgent(AgentProgram(Path(sys.executable).resolve(),
                (str(self.agent_path),), timeout_seconds=3))
        worker = LocalCodeWorker(self.actor,runner,health_directory=self.root)
        worker(Mission('job',REPO))
        self.assertEqual((self.root/'trees'/'job'/'goal.py').read_text(),"'feature.py works'")

    def test_persisted_project_goal_reaches_actual_local_agent(self):
        self.agent_path.write_text('''import json,sys
request=json.loads(sys.stdin.buffer.read().decode('utf-8'))
if request['action']=='health':
 print(json.dumps({'protocol':1,'action':'health','status':'ok','agent_ready':True}))
else:
 goal=request['payload']['project_goal']
 assert 'gestion de quêtes' in goal
 assert request['payload']['acceptance']==['feature.py works']
 print(json.dumps({'protocol':1,'action':'plan_patch','status':'ok',
 'plan':{'files':[{'path':'intent.txt','content':goal}],
         'commit_message':'feat: preserve project objective'}}))
''', encoding='utf-8')
        runner=LocalProcessAgent(AgentProgram(Path(sys.executable).resolve(),
              (str(self.agent_path),), timeout_seconds=8))
        worker=LocalCodeWorker(self.actor, runner, health_directory=self.root)
        worker(Mission('job',REPO))
        self.assertIn('gestion de quêtes',
                      (self.root/'trees'/'job'/'intent.txt').read_text(encoding='utf-8'))

    def test_corrupt_stored_project_goal_blocks_before_model(self):
        row=self.store.db.execute(
            "SELECT spec_json FROM mission_specs WHERE id='job'").fetchone()
        spec=json.loads(row[0])
        spec['project_goal']='X'*5000
        with self.store._transaction():
            self.store.db.execute("UPDATE mission_specs SET spec_json=? WHERE id='job'",
                                  (json.dumps(spec),))
        worker=self.worker(self.plan())
        from unittest.mock import patch
        with patch.object(worker.agent,'request',
                          side_effect=AssertionError('model launched')) as run:
            with self.assertRaisesRegex(CodePlanError,'Invalid stored project direction'):
                worker(Mission('job',REPO))
            run.assert_not_called()
        self.assertFalse((self.root/'trees'/'job').exists())

    def test_missing_roadmap_spec_refuses_to_start_work(self):
        with self.store._transaction():
            self.store.db.execute("DELETE FROM mission_specs WHERE id='job'")
        worker=self.worker(self.plan())
        with self.assertRaisesRegex(CodePlanError,'lacks immutable roadmap'):
            worker(Mission('job',REPO))
        self.assertFalse((self.root/'trees'/'job').exists())

    def test_model_cannot_modify_doctor_security_or_independent_tests(self):
        for path in ('app/doctor_gate.py', 'tools/DoctorAtlas/rules.py',
                     'app/authentication.py', 'docs/security_policy.md',
                     'holding/proofs.py', 'tests/test_real_doctor.py',
                     'app/permission_manager.py', 'src/ci_rules.py'):
            with self.subTest(path=path):
                worker = self.worker(self.plan(path))
                with self.assertRaisesRegex(CodePlanError, 'protected'):
                    worker(Mission('job', REPO))
                self.assertFalse((self.root/'trees'/'job'/path).exists())

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

    def test_code_only_grant_must_not_launch_model_or_create_worktree(self):
        self.store.revoke(REPO)
        self.store.grant_for_onboarding(StandingGrant(
            REPO, frozenset({'code'}), int(time.time())+500))
        worker = self.worker(self.plan())
        from unittest.mock import patch
        with patch.object(worker.agent, 'request',
                          side_effect=AssertionError('Model must not launch')) as run:
            with self.assertRaisesRegex(Exception, 'commit permission'):
                worker(Mission('job', REPO))
            run.assert_not_called()
        self.assertFalse((self.root/'trees'/'job').exists())

    def test_agent_direct_mutation_denied(self):
        worker=self.worker(self.plan(),direct_mutation=True)
        with self.assertRaisesRegex(CodePlanError,'modified worktree'):worker(Mission('job',REPO))
        self.assertFalse((self.root/'trees'/'job'/'src'/'feature.py').exists())

    def test_pause_during_model_response_prevents_any_patch_write(self):
        worker = self.worker(self.plan())
        original = worker.agent.request
        def paused_reply(action, payload, *, cwd):
            result = original(action, payload, cwd=cwd)
            if action == 'plan_patch':
                self.store.set_paused(True)
            return result
        worker.agent.request = paused_reply
        from holding.process_agent import AgentInterrupted
        with self.assertRaises(AgentInterrupted):
            worker(Mission('job', REPO))
        self.assertFalse((self.root/'trees'/'job'/'src'/'feature.py').exists())

    def test_no_healthy_worker_makes_no_git_changes(self):
        worker=self.worker(self.plan(),ready=False)
        self.assertFalse(worker.ready())
        with self.assertRaises(Exception):worker(Mission('job',REPO))
        self.assertFalse((self.root/'trees'/'job').exists())

if __name__=='__main__':unittest.main()

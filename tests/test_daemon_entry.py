import hashlib
import io
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
        # Windows runner temporary paths can include a junction/alias; the
        # installed pin must be canonical, not a raw tempfile alias.
        script = (self.root / 'viewer.py').resolve()
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

    def _enable_mcp(self):
        self._enable_dashboard(readonly=True)
        self.data['schema'] = 4
        executable = Path(sys.executable).resolve()
        self.data['mcp'] = {
            'interpreter': str(executable),
            'interpreter_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
            'response_timeout': 8,
        }

    def _enable_mcp_package_pin(self):
        from holding.mcp_package_pin import build_installation_module_pins
        self._enable_mcp()
        self.data['schema'] = 5
        root = Path(__file__).resolve().parents[1]
        pins = build_installation_module_pins(root)
        self.data['mcp']['module_sha256'] = dict(pins.entries)

    def _trusted_mcp_enrollment(self):
        from holding.mcp_package_pin import build_installation_module_pins
        self._enable_dashboard(readonly=True)
        self.save()
        interpreter = Path(sys.executable).resolve()
        root = Path(__file__).resolve().parents[1]
        pin = build_installation_module_pins(root)
        return {
            "source_config": self.config,
            "installed_package_root": root,
            "interpreter_path": interpreter,
            "trusted_interpreter_sha256":
                hashlib.sha256(interpreter.read_bytes()).hexdigest(),
            "trusted_module_sha256": dict(pin.entries),
            "timeout_seconds": 3,
        }

    def test_installer_prepares_and_exclusively_writes_real_mcp_v5_config(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        from holding.mcp_enrollment import (
            prepare_v5_mcp_enrollment, create_new_v5_config)
        context = (patch("holding.mcp_enrollment.require_private_windows_path")
                   if sys.platform == "win32" else nullcontext())
        with context:
            params = self._trusted_mcp_enrollment()
            prior = self.config.read_bytes()
            plan = prepare_v5_mcp_enrollment(**params)
            self.assertEqual(self.config.read_bytes(), prior)
            self.assertGreaterEqual(plan.pinned_module_count, 7)
            self.assertEqual(json.loads(plan.payload)["schema"], 5)
            output = self.root / "install-v5.json"
            self.assertEqual(create_new_v5_config(plan=plan, destination=output),
                             output)
            self.assertEqual(self.config.read_bytes(), prior)
            loaded = Installation.load(output)
            self.assertIsNotNone(loaded.mcp.package_pin)
            self.assertTrue(loaded.mcp.package_pin.verify(params["installed_package_root"]))
            with self.assertRaises(FileExistsError):
                create_new_v5_config(plan=plan, destination=output)
            self.assertEqual(output.read_bytes(), plan.payload)

    def test_installer_never_writes_outside_trusted_config_directory(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        from holding.mcp_enrollment import (
            prepare_v5_mcp_enrollment, create_new_v5_config, McpEnrollmentError)
        context = (patch("holding.mcp_enrollment.require_private_windows_path")
                   if sys.platform == "win32" else nullcontext())
        with context:
            params = self._trusted_mcp_enrollment()
            plan = prepare_v5_mcp_enrollment(**params)
            outside = Path(self.tmp.name).parent / "outside-holding.json"
            with self.assertRaises(McpEnrollmentError):
                create_new_v5_config(plan=plan, destination=outside)
            self.assertFalse(outside.exists())
            with self.assertRaises(McpEnrollmentError):
                create_new_v5_config(plan=plan, destination=self.config)
            with self.assertRaises(McpEnrollmentError):
                create_new_v5_config(
                    plan=plan, destination=self.root / "install-v5.txt")

    def test_installer_refuses_release_manifest_mismatch_or_config_rollback(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        from holding.mcp_enrollment import (
            prepare_v5_mcp_enrollment, create_new_v5_config, McpEnrollmentError)
        context = (patch("holding.mcp_enrollment.require_private_windows_path")
                   if sys.platform == "win32" else nullcontext())
        with context:
            params = self._trusted_mcp_enrollment()
            forged = dict(params)
            forged["trusted_interpreter_sha256"] = "0" * 64
            with self.assertRaises(McpEnrollmentError):
                prepare_v5_mcp_enrollment(**forged)
            forged = dict(params)
            forged["trusted_module_sha256"] = dict(
                params["trusted_module_sha256"], **{"mcp_entry.py": "0" * 64})
            with self.assertRaises(McpEnrollmentError):
                prepare_v5_mcp_enrollment(**forged)
            plan = prepare_v5_mcp_enrollment(**params)
            self.data["timeout_seconds"] = 4
            self.save()
            target = self.root / "config-enrolled.json"
            with self.assertRaises(McpEnrollmentError):
                create_new_v5_config(plan=plan, destination=target)
            self.assertFalse(target.exists())

    def test_installer_rejects_duplicate_keys_and_implicit_mcp_replacement(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        from holding.mcp_enrollment import (
            prepare_v5_mcp_enrollment, McpEnrollmentError)
        context = (patch("holding.mcp_enrollment.require_private_windows_path")
                   if sys.platform == "win32" else nullcontext())
        with context:
            params = self._trusted_mcp_enrollment()
            source = self.config.read_text(encoding="utf-8")
            self.config.write_text(source[:-1] + ', "schema":3}',
                                   encoding="utf-8")
            with self.assertRaisesRegex(McpEnrollmentError, "Duplicate"):
                prepare_v5_mcp_enrollment(**params)
            self._enable_mcp_package_pin()
            self.save()
            with self.assertRaisesRegex(McpEnrollmentError, "replace existing"):
                prepare_v5_mcp_enrollment(**params)

    def test_installer_never_accepts_frozen_app_as_mcp_interpreter(self):
        from unittest.mock import patch
        from holding.mcp_enrollment import (
            prepare_v5_mcp_enrollment, McpEnrollmentError)
        params = self._trusted_mcp_enrollment()
        with patch("holding.mcp_enrollment.sys.frozen", True, create=True):
            with self.assertRaisesRegex(McpEnrollmentError, "not a frozen EXE"):
                prepare_v5_mcp_enrollment(**params)

    def test_schema_five_ensures_every_installed_mcp_module_is_pinned(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        self._enable_mcp_package_pin()
        self.save()
        installation = Installation.load(self.config)
        self.assertIsNotNone(installation.mcp.package_pin)
        self.assertTrue(installation.mcp.package_pin.verify(
            Path(__file__).resolve().parents[1]))
        windows_guard = (
            patch('holding.mcp_supervisor.require_private_windows_path')
            if sys.platform == 'win32' else nullcontext())
        with windows_guard:
            with installation.build_service() as service:
                self.assertIsNotNone(service._mcp_supervisor)
                self.assertIsNotNone(
                    service._mcp_supervisor._session._package_pin)
                service.start()
                # No module startup or network access until an allowed tick.
                self.assertEqual(
                    service._mcp_supervisor._session.observe().state,
                    'NOT_STARTED')

    def test_frozen_executable_cannot_fake_embedded_mcp_python(self):
        from unittest.mock import patch
        self._enable_mcp_package_pin()
        self.save()
        with patch("holding.daemon_entry.sys.frozen", True, create=True):
            with self.assertRaisesRegex(BootstrapError, "bundled Python interpreter"):
                Installation.load(self.config)

    def test_schema_five_refuses_forged_removed_or_unknown_module_evidence(self):
        self._enable_mcp_package_pin()
        manifest = self.data['mcp']['module_sha256']
        self.data['mcp']['module_sha256'] = dict(
            manifest, **{'mcp_entry.py': '0' * 64})
        self.save()
        with self.assertRaisesRegex(BootstrapError, 'package code changed'):
            Installation.load(self.config)
        self._enable_mcp_package_pin()
        del self.data['mcp']['module_sha256']['mcp_entry.py']
        self.save()
        with self.assertRaises(BootstrapError):
            Installation.load(self.config)
        self._enable_mcp_package_pin()
        self.data['mcp']['module_sha256']['injected.py'] = 'a' * 64
        self.save()
        with self.assertRaisesRegex(BootstrapError, 'package code changed'):
            Installation.load(self.config)
        self._enable_mcp_package_pin()
        del self.data['mcp']['module_sha256']
        self.save()
        with self.assertRaisesRegex(BootstrapError, 'pinned MCP installation'):
            Installation.load(self.config)

    def test_schema_four_installs_real_local_mcp_and_observes_engine_state(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from unittest.mock import patch
        from holding.mcp_hub_journal import HubObservationJournal

        self._enable_mcp()
        self.save()
        installation = Installation.load(self.config)
        self.assertIsNotNone(installation.mcp)
        context = (
            patch('holding.mcp_supervisor.require_private_windows_path')
            if sys.platform == 'win32' else nullcontext()
        )
        with context:
            with installation.build_service() as service:
                self.assertIsNotNone(service._mcp_supervisor)
                # The provisioned hub must not start during installer load.
                self.assertEqual(
                    service._mcp_supervisor._session.observe().state,
                    'NOT_STARTED',
                )
                guard_patch = patch(
                    "holding.service.ResourceGuard.check",
                    return_value=SimpleNamespace(safe=True, reason=""))
                guard_patch.start()
                self.addCleanup(guard_patch.stop)
                service.start()
                self.assertEqual(service.run(max_ticks=1).action, 'COMPLETE')
                with HubObservationJournal(Path(self.data['database']), readonly=True) as journal:
                    first = journal.latest(now_epoch=int(time.time()))
                self.assertEqual(first.state, 'RESPONSIVE')
                self.assertIsNotNone(first.pid)
                with StateStore(Path(self.data['database'])) as store:
                    store.set_paused(True)
                self.assertEqual(service.run(max_ticks=1).action, 'WAIT')
                with HubObservationJournal(Path(self.data['database']), readonly=True) as journal:
                    paused = journal.latest(now_epoch=int(time.time()))
                self.assertEqual(paused.state, 'CLOSED')
                with StateStore(Path(self.data['database'])) as store:
                    store.set_paused(False)
                self.assertEqual(service.run(max_ticks=1).action, 'COMPLETE')
                with HubObservationJournal(Path(self.data['database']), readonly=True) as journal:
                    resumed = journal.latest(now_epoch=int(time.time()))
                self.assertEqual(resumed.state, 'RESPONSIVE')
                self.assertNotEqual(resumed.pid, first.pid)
            with HubObservationJournal(Path(self.data['database']), readonly=True) as journal:
                closed = journal.latest(now_epoch=int(time.time()))
            self.assertEqual(closed.state, 'CLOSED')

    def test_schema_four_rejects_unpinned_mcp_without_service_launch(self):
        self._enable_mcp()
        self.data['mcp']['interpreter_sha256'] = '0' * 64
        self.save()
        with self.assertRaisesRegex(BootstrapError, 'changed since onboarding'):
            Installation.load(self.config)
        self._enable_mcp()
        self.data['mcp']['interpreter'] = str(self.script)
        self.save()
        with self.assertRaisesRegex(BootstrapError, 'MCP interpreter'):
            Installation.load(self.config)
        self._enable_mcp()
        self.data['mcp']['response_timeout'] = True
        self.save()
        with self.assertRaisesRegex(BootstrapError, 'MCP interpreter settings'):
            Installation.load(self.config)

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

    def test_engine_frozen_selftest_checks_real_durable_storage_offline(self):
        from holding.daemon_entry import main
        self.assertEqual(main(['--self-test']), 0)
        # Selftest must not be an alternative way to run untrusted config.
        self.assertEqual(main(['--self-test', '--config', str(self.config)]), 2)

    def test_native_frozen_engine_ipc_mode_refuses_config_and_other_modes(self):
        from holding.daemon_entry import main, _native_ipc_smoke
        from unittest.mock import patch
        with patch("holding.daemon_entry._native_ipc_smoke",
                   return_value=0) as native:
            self.assertEqual(main(["--native-ipc-smoke"]), 0)
            native.assert_called_once_with()
            self.assertEqual(main(["--native-ipc-smoke", "--self-test"]), 2)
            self.assertEqual(main([
                "--native-ipc-smoke", "--config", str(self.config),
            ]), 2)
            native.assert_called_once_with()
        # The endpoint has no Linux fallback that could fake native Windows.
        if __import__("os").name != "nt":
            self.assertEqual(_native_ipc_smoke(), 2)

    def test_real_service_exposes_readonly_update_health_from_private_pipe(self):
        from unittest.mock import patch
        from holding.daemon_entry import _enable_update_health_ipc
        from holding.dashboard_ipc import DashboardIpcClient

        self.save()
        installation = Installation.load(self.config)
        key = b"u" * 32
        output = io.StringIO()
        with installation.build_service() as service:
            service.start()
            with patch("holding.daemon_entry.os.name", "nt"):
                port, engine_id = _enable_update_health_ipc(
                    service, io.BytesIO(key), output_stream=output,
                )
            self.assertEqual(json.loads(output.getvalue()), {
                "engine_id": engine_id, "port": port, "schema": 1,
            })
            state = DashboardIpcClient(
                port=port, secret=key, engine_id=engine_id,
            ).fetch()
            self.assertEqual(state.engine_id, engine_id)
            self.assertFalse(state.paused)
            self.assertFalse(state.stop_requested)
            self.assertIsNotNone(service._dashboard_ipc)
        self.assertIsNone(service._dashboard_ipc)

    def test_update_health_refuses_console_and_malformed_capability(self):
        from unittest.mock import Mock, patch
        from holding.daemon_entry import _enable_update_health_ipc

        class Console(io.BytesIO):
            def isatty(self):
                return True

        service = Mock()
        with patch("holding.daemon_entry.os.name", "nt"):
            for source in (Console(b"u" * 32), io.BytesIO(b"short"),
                           io.BytesIO(b"u" * 33)):
                with self.subTest(source=source):
                    with self.assertRaises(BootstrapError):
                        _enable_update_health_ipc(
                            service, source, output_stream=io.StringIO(),
                        )
        service.enable_dashboard_ipc.assert_not_called()

    def test_update_health_cli_requires_config_and_excludes_diagnostics(self):
        from unittest.mock import patch
        from holding.daemon_entry import main

        with self.assertRaises(SystemExit):
            main(["--update-health-handshake"])
        if __import__("os").name != "nt":
            self.save()
            self.assertEqual(main([
                "--update-health-handshake", "--config", str(self.config),
            ]), 2)
        with patch("holding.daemon_entry._enable_update_health_ipc") as health:
            self.assertEqual(main([
                "--update-health-handshake", "--self-test",
                "--config", str(self.config),
            ]), 2)
            self.assertEqual(main([
                "--update-health-handshake", "--native-ipc-smoke",
                "--config", str(self.config),
            ]), 2)
            health.assert_not_called()

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

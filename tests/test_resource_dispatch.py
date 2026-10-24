import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from holding.autonomy import Mission, StandingGrant
from holding.control import ControlPlane
from holding.resource_guard import ResourceReport, ResourceGuard
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore

REPO='r9327/Dof-Atlas'

class SimpleWorker:
    def __init__(self):self.calls=[]
    def ready(self):return True
    def __call__(self,job):self.calls.append(job.id);return 'a'*40

class ResourceDispatchTests(unittest.TestCase):
    def test_no_claim_under_pressure_then_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/'test.db';agent=SimpleWorker()
            with StateStore(db) as store:
                store.add_missions([Mission('a',REPO)])
                store.grant_for_onboarding(StandingGrant(REPO,frozenset({'code'}),int(time.time())+300))
            with HoldingService(ServiceSettings(db,idle_seconds=.05),agent,online=lambda:False) as service:
                service.start()
                with patch.object(ResourceGuard,'check',return_value=ResourceReport(False,'RAM_PRESSURE',999,123)):
                    self.assertEqual(service.run(max_ticks=1).action,'RESOURCE_PRESSURE')
                self.assertEqual(agent.calls,[])
                self.assertEqual(service.engine.store.missions()[0].state,'READY')
                self.assertEqual(service.run(max_ticks=1).action,'QA_PENDING')
                self.assertEqual(agent.calls,['a'])

    def test_persistent_stop_wins_over_resource_pressure(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/'test.db';agent=SimpleWorker()
            with HoldingService(ServiceSettings(db,idle_seconds=.05),agent,online=lambda:False) as service:
                service.start()
                with ControlPlane(db) as ui:ui.request_stop()
                with patch.object(ResourceGuard,'check',return_value=ResourceReport(False,'RAM_PRESSURE',0,0)):
                    self.assertEqual(service.run(max_ticks=1).action,'STOPPED')
                self.assertEqual(agent.calls,[])

if __name__=='__main__':unittest.main()

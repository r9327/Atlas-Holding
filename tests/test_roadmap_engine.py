import json
from pathlib import Path
import tempfile
import unittest
from holding.roadmap_import import RoadmapImporter
from holding.engine import HoldingEngine
from holding.autonomy import StandingGrant
from holding.state_store import StateStore, StoreError

REPO = 'r9327/Dof-Atlas'
SHA = 'a'*40

def roadmap(tasks, repo=REPO):
    return json.dumps({'repo': repo,'missions':tasks})

def task(mid,deps=(),priority=0):
    return {'id':mid,'operation':'code','acceptance':['test command passes'],
            'depends_on':list(deps),'priority':priority}

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.sqlite'
        self.store=StateStore(self.path);self.addCleanup(self.store.close)
        self.importer=RoadmapImporter(self.store)
    def test_atomic_import_with_dependencies(self):
        result=self.importer.import_text(roadmap([task('a'),task('b',['a']),task('c')]),source='user:1')
        self.assertEqual(result.added,('a','b','c'))
        self.assertEqual(len(self.store.missions()),3)
        self.assertEqual(len(result.digest),64)
    def test_reimport_is_idempotent(self):
        raw=roadmap([task('a')]); first=self.importer.import_text(raw,source='u1')
        second=self.importer.import_text(raw,source='u2')
        self.assertEqual(first.version,second.version)
        self.assertEqual(second.added,())
        self.assertEqual(len(self.store.missions()),1)
    def test_cycle_refused_no_partial_rows(self):
        with self.assertRaises(StoreError):self.importer.import_text(roadmap([task('a',['b']),task('b',['a'])]),source='u1')
        self.assertEqual(self.store.missions(),())
    def test_missing_dependency_refused(self):
        with self.assertRaises(StoreError):self.importer.import_text(roadmap([task('a',['absent'])]),source='u1')
    def test_conflicting_replan_refused_without_losing_state(self):
        self.importer.import_text(roadmap([task('a')]),source='u1')
        with self.assertRaises(StoreError):self.importer.import_text(roadmap([task('a',priority=5),task('b')]),source='u2')
        self.assertEqual(len(self.store.missions()),1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM roadmap_versions').fetchone()[0],1)
    def test_revision_preserves_old_and_adds_new(self):
        self.importer.import_text(roadmap([task('a')]),source='u1')
        updated=self.importer.import_text(roadmap([task('a'),task('b',['a'])]),source='u2')
        self.assertEqual(updated.preserved,('a',))
        self.assertEqual(updated.added,('b',))
    def test_forbidden_unknown_and_ambiguous(self):
        for raw in ('do what is best',roadmap([dict(task('a'),operation='disable_ci')]),roadmap([dict(task('a'),acceptance=[])])):
            with self.assertRaises(StoreError):self.importer.import_text(raw,source='u1')
    def test_survives_database_reopen(self):
        self.importer.import_text(roadmap([task('a')]),source='u1')
        self.store.close();self.store=StateStore(self.path);self.importer=RoadmapImporter(self.store)
        self.assertEqual(self.store.missions()[0].id,'a')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM mission_specs').fetchone()[0],1)
    def test_json_fenced_input(self):
        raw='```json\n'+roadmap([task('a')])+'\n```'
        self.assertEqual(self.importer.import_text(raw,source='u1').added,('a',))

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.sqlite'
        db=StateStore(self.path)
        RoadmapImporter(db).import_text(roadmap([task('a'),task('b'),task('c',['a'])]),source='u1')
        db.grant_for_onboarding(StandingGrant(REPO,frozenset({'code'}),int(__import__('time').time())+3600))
        db.close()
    def test_dispatch_produces_checkpoint_not_fake_pass(self):
        worker=lambda mission: SHA
        with HoldingEngine(self.path,worker,online=lambda:False) as engine:
            self.assertEqual(engine.start(),())
            self.assertEqual(engine.tick().action,'QA_PENDING')
            rows={m.id:m for m in engine.store.missions()}
            self.assertEqual(rows['a'].state,'QA_PENDING')
            self.assertEqual(rows['a'].evidence_sha,'')
            self.assertNotEqual(rows['a'].state,'DONE')
    def test_interrupted_running_is_quarantined_at_restart(self):
        with HoldingEngine(self.path,lambda m:SHA,online=lambda:False) as engine:
            engine.start()
            engine.store.claim_next(online=False)
        with HoldingEngine(self.path,lambda m:SHA,online=lambda:False) as engine:
            self.assertEqual(engine.start(),('a',))
            self.assertEqual(engine.store.missions()[0].state,'RECOVERY_REQUIRED')
            self.assertEqual(engine.tick().mission_id,'b')
    def test_pause_and_stop_are_safe(self):
        with HoldingEngine(self.path,lambda m:SHA,online=lambda:False) as engine:
            engine.start();engine.pause();self.assertEqual(engine.tick().action,'WAIT')
            engine.resume();engine.stop();self.assertEqual(engine.tick().action,'STOPPED')
    def test_failed_adapter_does_not_fake_success(self):
        def crash(_):raise RuntimeError('do not leak tokens')
        with HoldingEngine(self.path,crash,online=lambda:False) as engine:
            engine.start();out=engine.tick()
            self.assertEqual(out.action,'RETRY_OR_BLOCKED')
            self.assertTrue(all(m.state != 'DONE' for m in engine.store.missions()))
            self.assertNotIn('tokens',out.reason)

if __name__ == '__main__': unittest.main()

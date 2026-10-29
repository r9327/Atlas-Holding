"""Workplan-to-mission integration: no fake progress, no model permissions."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from holding import ollama_protocol
from holding.autonomy import StandingGrant
from holding.project_chat import ProjectChatCoordinator
from holding.research_workplan import (
    ResearchWorkplanCoordinator, WorkplanError, _validate,
)
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
CONFIG = {"schema":1,"model":"test-local:Q4","digest":"a"*64,
          "num_ctx":4096,"timeout":6}
BRIEF = {
    "concept":"Prototyper une interface adaptable.",
    "hypotheses":["Le concept peut être testé avec un prototype."],
    "experiments":[{"name":"Essai","method":"Tester une maquette.",
                    "success_measure":"Trois critères mesurables."}],
    "prototype_steps":["Créer le module logiciel."],
    "unknowns":["Efficacité du prototype inconnue."],
}
WORK = {"tasks":[
    {"kind":"code","title":"Créer module",
     "acceptance":["Un test unitaire vérifie le module."],"depends_on":[]},
    {"kind":"hardware","title":"Fabriquer prototype",
     "acceptance":["Vérifier les dimensions."],"depends_on":[0]},
    {"kind":"code","title":"Intégrer le matériel",
     "acceptance":["Tests d'intégration matériels."],"depends_on":[1]},
    {"kind":"code","title":"Créer tests supplémentaires",
     "acceptance":["Tests unitaires vérifiables."],"depends_on":[0]},
]}


class WorkplanTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db=Path(tmp.name)/"hold.sqlite"
        self.config=Path(tmp.name)/"model.json"
        self.config.write_text(json.dumps(CONFIG),encoding="utf-8")
        self.store=StateStore(self.db)
        self.addCleanup(self.store.close)
        self.chat=ProjectChatCoordinator(
            self.store,repository=REPO,config_path=self.config)
        self.work=ResearchWorkplanCoordinator(
            self.store,repository=REPO,config_path=self.config)

    def create(self, work=WORK):
        responses=iter([
            {"message":{"content":json.dumps({"questions":[]})}},
            {"message":{"content":json.dumps(BRIEF,ensure_ascii=False)}},
            {"message":{"content":json.dumps(work,ensure_ascii=False)}},
        ])
        with patch.object(ollama_protocol,"_installed_model"), \
             patch.object(ollama_protocol,"_http",
                          side_effect=lambda *args,**kwargs:next(responses)) as http:
            view=self.chat.start("Concevoir et développer un prototype logiciel.")
        return view,http

    def test_auto_stage_only_software_with_software_dependencies(self):
        v,http=self.create()
        self.assertEqual(http.call_count,3)
        self.assertEqual(v.status,"READY")
        self.assertEqual(v.workplan_state,"STAGED")
        self.assertEqual((v.queued_tasks,v.deferred_tasks),(2,2))
        work=self.work.inspect(v.session_id)
        self.assertEqual(len(work.tasks),4)
        self.assertEqual(len(work.queued),2)
        self.assertEqual(len(work.deferred),2)
        missions={m.id:m for m in self.store.missions()}
        self.assertEqual(len(missions),3)
        self.assertIn(v.mission_id,missions)
        self.assertEqual(missions[v.mission_id].operation,"plan")
        self.assertEqual(missions[v.mission_id].state,"READY")
        self.assertEqual(missions[work.queued[0]].dependencies,())
        self.assertEqual(missions[work.queued[1]].dependencies,(work.queued[0],))
        self.assertTrue(all(k not in missions for k in work.deferred))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM grants").fetchone()[0],0)
        self.assertEqual(self.store.claim_next(
            online=False,supported_operations=frozenset({"code"})).action,
            "REQUIRES_DECISION")
        specs=[json.loads(r[0]) for r in self.store.db.execute(
            "SELECT spec_json FROM mission_specs ORDER BY id")]
        self.assertEqual({x["project_goal"] for x in specs},
                         {"Concevoir et développer un prototype logiciel."})
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM project_workplans").fetchone()[0],1)
        self.assertEqual(len([x for x in self.store.events()
                              if x["event"]=="RND_WORKPLAN_STAGED"]),1)
        self.store.grant_for_onboarding(StandingGrant(
            REPO,frozenset({"code"}),int(time.time())+600))
        choice=self.store.claim_next(
            online=False,supported_operations=frozenset({"code"}))
        self.assertEqual(choice.action,"RUN")
        self.assertEqual(choice.mission_id,work.queued[0])

    def test_restart_never_regenerates_research_or_model_plan(self):
        v,_=self.create()
        with StateStore(self.db) as reopened:
            chat=ProjectChatCoordinator(
                reopened,repository=REPO,config_path=self.config)
            with patch.object(ollama_protocol,"_http",
                              side_effect=AssertionError("model must not run")):
                again=chat.resume(v.session_id)
                self.assertEqual(again.workplan_state,"STAGED")
        self.assertEqual(len(self.store.missions()),3)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_amendments").fetchone()[0],1)

    def test_noncode_only_is_deferred_and_never_granted(self):
        tasks={"tasks":[
            {"kind":"research","title":"Étudier le phénomène",
             "acceptance":["Comparer deux hypothèses."],"depends_on":[]},
            {"kind":"simulation","title":"Simuler une expérience",
             "acceptance":["Définir un modèle."],"depends_on":[0]},
        ]}
        v,_=self.create(tasks)
        self.assertEqual((v.queued_tasks,v.deferred_tasks),(0,2))
        self.assertEqual(len(self.store.missions()),1)
        self.assertEqual(self.work.inspect(v.session_id).state,"STAGED")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM grants").fetchone()[0],0)

    def test_malformed_plan_never_adds_a_code_mission(self):
        invalid=[
            {"tasks":[]},
            {"tasks":[{"kind":"shell","title":"Command",
                       "acceptance":["Run"],"depends_on":[]}]},
            {"tasks":[{"kind":"code","title":"future",
                       "acceptance":["ok"],"depends_on":[1]}]},
            {"tasks":[{"kind":"code","title":"boolean",
                       "acceptance":["ok"],"depends_on":[True]}]},
            {"tasks":[{"kind":"code","title":"invalid",
                       "acceptance":["ok"],"depends_on":[],
                       "shell":"test"}]},
        ]
        for plan in invalid:
            with self.subTest(plan=plan),self.assertRaises(WorkplanError):
                _validate(json.dumps(plan))
        with self.assertRaises(WorkplanError):
            _validate('{"tasks":[],"tasks":[]}')
        with self.assertRaises(WorkplanError):
            _validate(chr(0xD800))
        with self.assertRaises(WorkplanError):
            _validate("X"*40000)

    def test_invalid_model_workplan_keeps_research_but_not_code(self):
        responses=iter([
            {"message":{"content":json.dumps({"questions":[]})}},
            {"message":{"content":json.dumps(BRIEF,ensure_ascii=False)}},
            {"message":{"content":json.dumps({"tasks":[]})}},
        ])
        with patch.object(ollama_protocol,"_installed_model"), \
             patch.object(ollama_protocol,"_http",
                          side_effect=lambda *a,**k:next(responses)):
            with self.assertRaises(WorkplanError):
                self.chat.start("Concevoir un logiciel pilotant un objet.")
        v=self.chat.latest()
        self.assertEqual(v.status,"READY")
        self.assertIsNone(v.workplan_state)
        self.assertEqual(len(self.store.missions()),1)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM research_briefs").fetchone()[0],1)

    def test_import_failure_after_commit_is_resumable_without_duplicates(self):
        from holding.roadmap_replan import SafeRoadmapReplanner
        saved=SafeRoadmapReplanner.append_direction
        def committed_then_crash(replanner, *args, **kwargs):
            saved(replanner,*args,**kwargs)
            raise TimeoutError("simulated crash after roadmap commit")
        replies=iter([
            {"message":{"content":json.dumps({"questions":[]})}},
            {"message":{"content":json.dumps(BRIEF,ensure_ascii=False)}},
            {"message":{"content":json.dumps(WORK)}},
        ])
        with patch.object(ollama_protocol,"_installed_model"), \
             patch.object(ollama_protocol,"_http",
                          side_effect=lambda *a,**k:next(replies)), \
             patch.object(SafeRoadmapReplanner,"append_direction",
                          committed_then_crash):
            with self.assertRaises(TimeoutError):
                self.chat.start("Concevoir un logiciel autonome.")
        v=self.chat.latest()
        self.assertEqual(v.status,"READY")
        self.assertEqual(v.workplan_state,"PREPARED")
        self.assertEqual(len(self.store.missions()),3)
        with patch.object(ollama_protocol,"_http",
                          side_effect=AssertionError("do not regenerate")):
            resumed=self.chat.resume(v.session_id)
        self.assertEqual(resumed.workplan_state,"STAGED")
        self.assertEqual(len(self.store.missions()),3)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_amendments").fetchone()[0],1)

    def test_pause_prevents_transferring_prepared_plan(self):
        from holding.roadmap_replan import SafeRoadmapReplanner
        original=SafeRoadmapReplanner.append_direction
        def paused(replanner,*args,**kwargs):
            self.store.set_paused(True)
            return original(replanner,*args,**kwargs)
        responses=iter([
            {"message":{"content":json.dumps({"questions":[]})}},
            {"message":{"content":json.dumps(BRIEF,ensure_ascii=False)}},
            {"message":{"content":json.dumps(WORK)}},
        ])
        with patch.object(ollama_protocol,"_installed_model"), \
             patch.object(ollama_protocol,"_http",
                          side_effect=lambda *a,**k:next(responses)), \
             patch.object(SafeRoadmapReplanner,"append_direction",paused):
            with self.assertRaises(WorkplanError):
                self.chat.start("Développer un outil logiciel.")
        self.assertEqual(self.chat.latest().workplan_state,"PREPARED")
        self.store.set_paused(False)
        self.assertEqual(self.chat.resume(self.chat.latest().session_id).workplan_state,
                         "STAGED")


if __name__=="__main__":
    unittest.main()

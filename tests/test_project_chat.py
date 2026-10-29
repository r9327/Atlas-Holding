"""Project chat: clarification, automatic first R&D handoff and restart."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding import ollama_protocol
from holding.project_chat import (
    ProjectChatCoordinator, ProjectChatError, _questions,
)
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
CONFIG = {"schema": 1, "model": "chat-test:Q4", "digest": "b" * 64,
          "num_ctx": 4096, "timeout": 6}
WORKPLAN = {"tasks": [
    {"kind": "code", "title": "Créer le prototype logiciel",
     "acceptance": ["Ajouter un module de démarrage"], "depends_on": []},
    {"kind": "hardware", "title": "Préparer le montage",
     "acceptance": ["Documenter les composants requis"], "depends_on": [0]},
]}
PROPOSAL = {
    "concept": "Prototype d'interface accessible.",
    "hypotheses": ["Les utilisateurs souhaitent une interface simple."],
    "experiments": [{"name": "Test utilisateur", "method": "Comparer deux maquettes.",
                     "success_measure": "Deux critères observables."}],
    "prototype_steps": ["Dessiner un écran utilisable."],
    "unknowns": ["Coût réel des composants."],
}


class ProjectChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "holding.sqlite"
        self.config = Path(self.tmp.name) / "pinned-model.json"
        self.config.write_text(json.dumps(CONFIG), encoding="utf-8")
        self.store = StateStore(self.db)
        self.addCleanup(self.store.close)
        self.chat = ProjectChatCoordinator(
            self.store, repository=REPO, config_path=self.config)

    def inference(self, questions):
        replies = iter([
            {"message": {"content": json.dumps({"questions": questions},
                                               ensure_ascii=False)}},
            {"message": {"content": json.dumps(PROPOSAL, ensure_ascii=False)}},
            {"message": {"content": json.dumps(WORKPLAN, ensure_ascii=False)}},
        ])
        return patch.object(ollama_protocol, "_http",
                            side_effect=lambda *args, **kwargs: next(replies))

    def test_zero_questions_starts_research_and_stores_draft_automatically(self):
        with patch.object(ollama_protocol, "_installed_model"), self.inference([]) as http:
            result = self.chat.start(
                "Créer un outil qui aide à dessiner des interfaces accessibles.")
        self.assertEqual(result.status, "READY")
        self.assertEqual(result.questions, ())
        self.assertEqual(result.concept, PROPOSAL["concept"])
        self.assertEqual(result.research_brief, PROPOSAL)
        self.assertIn("Créer le prototype logiciel", result.task_details[0])
        self.assertIn("transmise au moteur", result.task_details[0])
        self.assertIn("prérequis : tâches 1", result.task_details[1])
        self.assertEqual(len(result.proposal_sha256), 64)
        self.assertEqual(http.call_count, 3)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM research_briefs").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM grants").fetchone()[0], 0)
        research = next(m for m in self.store.missions() if m.id == result.mission_id)
        self.assertEqual(research.state, "READY")
        self.assertEqual(research.operation, "plan")
        self.assertEqual(result.workplan_state, "STAGED")
        self.assertEqual((result.queued_tasks, result.deferred_tasks), (1, 1))
        self.assertEqual(len(self.store.missions()), 2)
        with patch.object(ollama_protocol, "_http",
                          side_effect=AssertionError("must be cached")):
            self.assertEqual(self.chat.resume(result.session_id).status, "READY")
        with StateStore(self.db) as reopened:
            self.assertEqual(
                ProjectChatCoordinator(reopened, repository=REPO,
                                       config_path=self.config).latest().concept,
                PROPOSAL["concept"])

    def test_one_project_per_repository_rejects_duplicate_conversation(self):
        with patch.object(ollama_protocol, "_installed_model"), self.inference([]):
            self.chat.start("Concevoir un outil de modélisation accessible.")
        with patch.object(ollama_protocol, "_http",
                          side_effect=AssertionError("second project cannot run")):
            with self.assertRaisesRegex(ProjectChatError,
                                        "already has a chat project"):
                self.chat.start("Autre projet dans le même dépôt.")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM project_chat_sessions").fetchone()[0], 1)

    def test_two_questions_asked_only_once_then_automatic_start(self):
        queries = ["Qui utilisera l'outil ?", "Quel est son usage prioritaire ?"]
        with patch.object(ollama_protocol, "_installed_model"), self.inference(queries) as http:
            first = self.chat.start("Créer un outil.")
            self.assertEqual(first.status, "ASKING")
            self.assertEqual(first.next_question, queries[0])
            self.assertEqual(http.call_count, 1)
            halfway = self.chat.answer(first.session_id, "Des apprentis.")
            self.assertEqual(halfway.status, "ASKING")
            self.assertEqual(halfway.next_question, queries[1])
            self.assertEqual(http.call_count, 1)
            done = self.chat.answer(first.session_id, "Faire des maquettes.")
            self.assertEqual(done.status, "READY")
            self.assertEqual(http.call_count, 3)
        spec = json.loads(self.store.db.execute(
            "SELECT spec_json FROM mission_specs WHERE id=?", (done.mission_id,)
        ).fetchone()[0])
        self.assertIn("Des apprentis.", spec["project_goal"])
        self.assertIn("Faire des maquettes.", spec["project_goal"])
        self.assertEqual(done.answers, ("Des apprentis.", "Faire des maquettes."))
        with self.assertRaisesRegex(ProjectChatError, "No outstanding"):
            self.chat.answer(first.session_id, "Un autre objectif.")

    def test_crash_after_generation_is_resumable_without_duplicate_import(self):
        replies = iter([
            {"message": {"content": json.dumps({"questions": []})}},
            {"message": {"content": json.dumps(PROPOSAL)}},
        ])
        def once(route_method, endpoint, **kwargs):
            result = next(replies)
            if endpoint == "/api/chat" and kwargs["payload"]["format"].get("required") == [
                    "concept", "hypotheses", "experiments", "prototype_steps", "unknowns"]:
                raise TimeoutError("model crash")
            return result
        with patch.object(ollama_protocol, "_installed_model"), \
             patch.object(ollama_protocol, "_http", side_effect=once):
            with self.assertRaises(TimeoutError):
                self.chat.start("Créer un système de prototypage accessible.")
        prior = self.chat.latest()
        self.assertEqual(prior.status, "GENERATING")
        self.assertEqual(len(self.store.missions()), 1)
        responses = iter([
            {"message": {"content": json.dumps(PROPOSAL)}},
            {"message": {"content": json.dumps(WORKPLAN)}},
        ])
        with patch.object(ollama_protocol, "_installed_model"), \
             patch.object(ollama_protocol, "_http",
                          side_effect=lambda *a, **kw: next(responses)):
            done = self.chat.resume(prior.session_id)
        self.assertEqual(done.status, "READY")
        self.assertEqual(len(self.store.missions()), 2)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_versions").fetchone()[0], 2)

    def test_paused_state_never_assesses_or_imports(self):
        self.store.set_paused(True)
        with patch.object(ollama_protocol, "_http",
                          side_effect=AssertionError("no model")):
            with self.assertRaisesRegex(ProjectChatError, "paused"):
                self.chat.start("Concevoir un système R&D fiable.")
        self.assertIsNone(self.chat.latest())
        self.store.set_paused(False)

    def test_malformed_questions_refuse_without_mission(self):
        bad = [
            {"questions": ["A?", "B?", "C?", "D?"]},
            {"questions": ["Question ?", "question ?"]},
            {"questions": [42]},
            {"questions": [], "shell": "rm -rf"},
        ]
        for response in bad:
            with self.subTest(response=response):
                with patch.object(ollama_protocol, "_installed_model"), \
                     patch.object(ollama_protocol, "_http",
                                  return_value={"message": {"content": json.dumps(response)}}):
                    with self.assertRaises(ProjectChatError):
                        self.chat.start("Créer un prototype original.")
        self.assertEqual(self.store.missions(), ())
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM research_briefs"
            if self.store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='research_briefs'"
            ).fetchone() else "SELECT 0").fetchone()[0], 0)

    def test_question_validator_limits_and_rejects_duplicates(self):
        self.assertEqual(_questions('{"questions":[]}'), ())
        self.assertEqual(_questions('{"questions":["Pour qui ?"]}'), ("Pour qui ?",))
        for invalid in ('{"questions":["A?","A?"]}', '{"questions":null}',
                        '{"questions":[],"questions":[]}', '{"questions":["' +
                        "Q" * 300 + '"]}', '[1,2]'):
            with self.subTest(invalid=invalid[:25]), self.assertRaises(ProjectChatError):
                _questions(invalid)

    def test_invalid_repo_goal_and_foreign_session_are_rejected(self):
        with self.assertRaises(ProjectChatError):
            ProjectChatCoordinator(
                self.store, repository="../wrong", config_path=self.config)
        for message in ("", " " * 3, "X" * 1500, "\ud800"):
            with self.subTest(message=str(message)[:12]), \
                    self.assertRaises(ProjectChatError):
                self.chat.start(message)
        with self.assertRaises(ProjectChatError):
            self.chat.view("a" * 32)
        with self.assertRaises(ProjectChatError):
            self.chat.view("../foreign")

    def test_asking_session_persists_after_reopen_without_reassessment(self):
        with patch.object(ollama_protocol, "_installed_model"), \
             patch.object(ollama_protocol, "_http",
                          return_value={"message": {
                              "content": '{"questions":["Quelle échelle ?"]}'}}):
            first = self.chat.start("Concevoir un atelier robotisé.")
        with StateStore(self.db) as other:
            chat = ProjectChatCoordinator(other, repository=REPO,
                                          config_path=self.config)
            with patch.object(ollama_protocol, "_http",
                              side_effect=AssertionError("repeat assessment")):
                self.assertEqual(chat.resume(first.session_id).next_question,
                                 "Quelle échelle ?")


if __name__ == "__main__":
    unittest.main()

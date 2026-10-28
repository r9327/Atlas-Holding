"""Offline research/design brief tests; no real model or outside network."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding import ollama_protocol
from holding.daemon_cli import main as daemon_main
from holding.research_brief import (
    ResearchBriefError, ResearchBriefService, STATUS, _validate_proposal,
)
from holding.roadmap_import import RoadmapImporter
from holding.roadmap_replan import SafeRoadmapReplanner
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
MODEL = "test-research:Q4"
CONFIG = {"schema": 1, "model": MODEL, "digest": "a" * 64,
          "num_ctx": 4096, "timeout": 5}
PROPOSAL = {
    "concept": "Concevoir un outil de conception accessible.",
    "hypotheses": ["Les utilisateurs ont besoin d'un prototype simple."],
    "experiments": [{
        "name": "Prototype exploratoire",
        "method": "Comparer deux concepts de maquettes sans installer de logiciel.",
        "success_measure": "Trois scénarios testables sont décrits.",
    }],
    "prototype_steps": ["Préparer une maquette limitée."],
    "unknowns": ["La demande réelle des utilisateurs reste inconnue."],
}


def roadmap(*, goal="Concevoir des outils d'exploration créative.",
            operation="plan", acceptance=None):
    return json.dumps({
        "repo": REPO, "project_goal": goal, "missions": [{
            "id": "research-1", "operation": operation,
            "acceptance": acceptance or ["Proposer des hypothèses testables"],
        }],
    }, ensure_ascii=False)


class ResearchBriefTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "state.sqlite"
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        RoadmapImporter(self.store).import_text(roadmap(), source="operator:goal")
        self.config_path = self.root / "pinned-model.json"
        self.config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
        self.service = ResearchBriefService(self.store)

    def run_draft(self, response=None):
        payload = {"message": {"content": json.dumps(
            PROPOSAL if response is None else response,
            ensure_ascii=False)}}
        with patch.object(ollama_protocol, "_installed_model") as check, \
                patch.object(ollama_protocol, "_http", return_value=payload) as chat:
            receipt = self.service.prepare("research-1", config_path=self.config_path)
        return receipt, chat, check

    def test_plan_mission_generates_cached_auditable_proposal_without_done(self):
        receipt, chat, check = self.run_draft()
        self.assertEqual(receipt.status, STATUS)
        self.assertFalse(receipt.cached)
        self.assertEqual(receipt.brief, PROPOSAL)
        self.assertEqual(receipt.model_digest, CONFIG["digest"])
        self.assertEqual(receipt.repository, REPO)
        self.assertEqual(len(receipt.proposal_sha256), 64)
        self.assertEqual(self.store.missions()[0].state, "READY")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM grants").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM research_briefs").fetchone()[0], 1)
        check.assert_called_once()
        sent = chat.call_args.kwargs["payload"]
        self.assertEqual(sent["model"], MODEL)
        self.assertFalse(sent["stream"])
        self.assertEqual(sent["messages"][0]["role"], "system")
        self.assertNotIn(PROPOSAL["concept"], sent["messages"][0]["content"])
        request = json.loads(sent["messages"][1]["content"])
        self.assertIn("exploration créative", request["project_goal"])
        self.assertEqual(request["acceptance"], ["Proposer des hypothèses testables"])
        self.assertNotIn("secrets", request)

        with patch.object(ollama_protocol, "_installed_model",
                          side_effect=AssertionError("model should stay idle")), \
                patch.object(ollama_protocol, "_http",
                             side_effect=AssertionError("no new generation")):
            again = self.service.prepare("research-1", config_path=self.config_path)
        self.assertTrue(again.cached)
        self.assertEqual(again.proposal_sha256, receipt.proposal_sha256)
        self.assertEqual(len([e for e in self.store.events()
                              if e["event"] == "RESEARCH_PROPOSAL_RECORDED"]), 1)

    def test_cli_returns_real_unverified_proposal_without_completing_mission(self):
        with patch.object(ollama_protocol, "_installed_model"), \
                patch.object(ollama_protocol, "_http",
                             return_value={"message": {"content": json.dumps(PROPOSAL)}}):
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(daemon_main([
                    "--db", str(self.path), "research-brief",
                    "--mission-id", "research-1",
                    "--model-config", str(self.config_path),
                ]), 0)
        response = json.loads(out.getvalue())
        self.assertEqual(response["status"], STATUS)
        self.assertEqual(response["brief"]["concept"], PROPOSAL["concept"])
        self.assertEqual(self.store.missions()[0].state, "READY")

    def test_fresh_database_does_not_generate_without_imported_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            with StateStore(Path(directory) / "blank.sqlite") as blank:
                with self.assertRaisesRegex(ResearchBriefError, "Import a plan"):
                    ResearchBriefService(blank).prepare(
                        "research-1", config_path=self.config_path)

    def test_invalid_unicode_project_goal_is_rejected_before_generation(self):
        with self.store._transaction():
            spec = json.loads(self.store.db.execute(
                "SELECT spec_json FROM mission_specs WHERE id='research-1'"
            ).fetchone()[0])
            spec["project_goal"] = "\ud800"
            self.store.db.execute(
                "UPDATE mission_specs SET spec_json=? WHERE id='research-1'",
                (json.dumps(spec),))
        with patch.object(ollama_protocol, "_http",
                          side_effect=AssertionError("unexpected model call")):
            with self.assertRaises(ResearchBriefError):
                self.service.prepare("research-1", config_path=self.config_path)

    def test_operator_must_select_imported_plan_and_explicit_goal(self):
        for task_id in ("absent", "../unsafe", "research-1; drop table"):
            with self.subTest(task_id=task_id), \
                    patch.object(ollama_protocol, "_http",
                                 side_effect=AssertionError("model called")):
                with self.assertRaises(ResearchBriefError):
                    self.service.prepare(task_id, config_path=self.config_path)
        with self.store._transaction():
            self.store.db.execute(
                "UPDATE missions SET operation='code' WHERE id='research-1'")
        with self.assertRaises(ResearchBriefError):
            self.service.prepare("research-1", config_path=self.config_path)
        with self.store._transaction():
            self.store.db.execute(
                "UPDATE missions SET operation='plan' WHERE id='research-1'")
            spec = json.loads(self.store.db.execute(
                "SELECT spec_json FROM mission_specs WHERE id='research-1'").fetchone()[0])
            spec.pop("project_goal")
            self.store.db.execute(
                "UPDATE mission_specs SET spec_json=? WHERE id='research-1'",
                (json.dumps(spec),))
        with self.assertRaises(ResearchBriefError):
            self.service.prepare("research-1", config_path=self.config_path)

    def test_paused_or_stopped_operator_cannot_generate(self):
        self.store.set_paused(True)
        with patch.object(ollama_protocol, "_installed_model",
                          side_effect=AssertionError("model launched")):
            with self.assertRaisesRegex(ResearchBriefError, "paused"):
                self.service.prepare("research-1", config_path=self.config_path)
        self.store.set_paused(False)
        with self.store._transaction():
            self.store.db.execute(
                "INSERT INTO settings(key,value) VALUES('stop_requested','1')")
        with self.assertRaisesRegex(ResearchBriefError, "stopped"):
            self.service.prepare("research-1", config_path=self.config_path)

    def test_invalid_model_response_never_persists_or_certifies(self):
        invalids = [
            dict(PROPOSAL, certified=True),
            dict(PROPOSAL, hypotheses=[]),
            dict(PROPOSAL, experiments=[{"name": "Missing"}]),
            dict(PROPOSAL, concept="X" * 4000),
            "nonsense",
        ]
        for item in invalids:
            with self.subTest(item=str(item)[:25]):
                with patch.object(ollama_protocol, "_installed_model"), \
                        patch.object(ollama_protocol, "_http",
                                     return_value={"message": {
                                         "content": (item if isinstance(item, str)
                                                     else json.dumps(item))}}):
                    with self.assertRaises(ResearchBriefError):
                        self.service.prepare("research-1",
                                             config_path=self.config_path)
                exists = self.store.db.execute(
                    "SELECT name FROM sqlite_master WHERE name='research_briefs'"
                ).fetchone()
                self.assertIsNone(exists)
        with self.assertRaises(ResearchBriefError):
            _validate_proposal('{"concept":"one","concept":"two"}')
        with self.assertRaises(ResearchBriefError):
            _validate_proposal("X" * (64 * 1024 + 1))

    def test_model_health_failure_has_no_recorded_proposal(self):
        with patch.object(ollama_protocol, "_installed_model",
                          side_effect=ollama_protocol.AdapterError(
                              "Pinned model unavailable")), \
                patch.object(ollama_protocol, "_http",
                             side_effect=AssertionError("must not infer")):
            with self.assertRaises(ollama_protocol.AdapterError):
                self.service.prepare("research-1", config_path=self.config_path)
        self.assertFalse(any(e["event"] == "RESEARCH_PROPOSAL_RECORDED"
                             for e in self.store.events()))

    def test_pause_during_generation_cannot_write_artifact(self):
        def suspended(*args, **kwargs):
            self.store.set_paused(True)
            return {"message": {"content": json.dumps(PROPOSAL)}}
        with patch.object(ollama_protocol, "_installed_model"), \
                patch.object(ollama_protocol, "_http", side_effect=suspended):
            with self.assertRaisesRegex(ResearchBriefError, "interrupted"):
                self.service.prepare("research-1", config_path=self.config_path)
        self.assertIsNone(self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE name='research_briefs'"
        ).fetchone())

    def test_revised_mission_cannot_keep_obsolete_brief_as_current(self):
        first, _, _ = self.run_draft()
        replan = SafeRoadmapReplanner(self.store)
        new = roadmap(acceptance=["Nouvelles expériences et hypothèses nécessaires"])
        changed = replan.apply(new, source="operator:changed-criteria")
        self.assertEqual(changed.revised, ("research-1",))
        second, _, _ = self.run_draft()
        self.assertNotEqual(second.spec_sha256, first.spec_sha256)
        self.assertFalse(second.cached)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM research_briefs").fetchone()[0], 2)
        self.assertEqual(self.store.missions()[0].state, "READY")

    def test_modified_goal_mid_inference_is_rejected_without_artifact(self):
        def changed(*args, **kwargs):
            spec = json.loads(self.store.db.execute(
                "SELECT spec_json FROM mission_specs WHERE id='research-1'"
            ).fetchone()[0])
            spec["project_goal"] = "Autre objectif"
            with self.store._transaction():
                self.store.db.execute(
                    "UPDATE mission_specs SET spec_json=? WHERE id='research-1'",
                    (json.dumps(spec),))
            return {"message": {"content": json.dumps(PROPOSAL)}}
        with patch.object(ollama_protocol, "_installed_model"), \
                patch.object(ollama_protocol, "_http", side_effect=changed):
            with self.assertRaisesRegex(ResearchBriefError, "changed"):
                self.service.prepare("research-1", config_path=self.config_path)
        self.assertIsNone(self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE name='research_briefs'"
        ).fetchone())

    def test_corrupt_cached_proposal_cannot_be_trusted(self):
        self.run_draft()
        with self.store._transaction():
            self.store.db.execute("UPDATE research_briefs SET proposal_json='{}'")
        with self.assertRaisesRegex(ResearchBriefError, "integrity"):
            self.service.prepare("research-1", config_path=self.config_path)

    def test_cli_reports_failure_without_raw_prompt_or_traceback(self):
        with redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as caught:
                daemon_main(["--db", str(self.path), "research-brief",
                             "--mission-id", "no-such-id",
                             "--model-config", str(self.config_path)])
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn("exploration créative", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()

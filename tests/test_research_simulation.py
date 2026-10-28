"""Offline numerical simulation contract: calculations, provenance and limits."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from holding import ollama_protocol
from holding.project_chat import ProjectChatCoordinator
from holding.research_simulation import (
    LocalSimulationRunner, SimulationError, compute_simulation,
    validate_simulation_spec,
)
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
CONFIG = {"schema": 1, "model": "simulation-test:Q4", "digest": "a"*64,
          "num_ctx": 4096, "timeout": 5}
BRIEF = {
    "concept": "Évaluer un prototype conceptuel.",
    "hypotheses": ["Les paramètres doivent être mesurés ultérieurement."],
    "experiments": [{
        "name": "Calcul local", "method": "Calculer une évolution hypothétique.",
        "success_measure": "Une série numérique reproductible.",
    }],
    "prototype_steps": ["Définir les paramètres."],
    "unknowns": ["Les entrées ne sont pas mesurées."],
}
LINEAR = {"model": "linear_discrete", "initial": 10, "parameter": 3, "steps": 4}
DECAY = {"model": "decay_discrete", "initial": 1000,
         "parameter": 8000, "steps": 2}


def task(kind, title, deps=(), simulation=None):
    row = {"kind": kind, "title": title,
           "acceptance": ["Produire un résultat contrôlable."],
           "depends_on": list(deps)}
    if simulation is not None:
        row["simulation"] = simulation
    return row


class NumericalSimulationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "holding.sqlite"
        self.config = Path(tmp.name) / "model.json"
        self.config.write_text(json.dumps(CONFIG), encoding="utf-8")
        self.store = StateStore(self.db)
        self.addCleanup(self.store.close)
        self.chat = ProjectChatCoordinator(
            self.store, repository=REPO, config_path=self.config)
        self.runner = LocalSimulationRunner(self.store)

    def start(self, plan):
        replies = iter([
            {"message": {"content": '{"questions":[]}'}},
            {"message": {"content": json.dumps(BRIEF, ensure_ascii=False)}},
            {"message": {"content": json.dumps(plan, ensure_ascii=False)}},
        ])
        with patch.object(ollama_protocol, "_installed_model"), \
             patch.object(ollama_protocol, "_http",
                          side_effect=lambda *a, **kw: next(replies)):
            return self.chat.start(
                "Créer un dispositif, comparer des modèles numériques et vérifier les hypothèses.")

    def test_real_computation_is_integer_bounded_and_reproducible(self):
        self.assertEqual(compute_simulation(LINEAR)["series"], [10, 13, 16, 19, 22])
        self.assertEqual(compute_simulation(DECAY)["series"], [1000, 800, 640])
        self.assertEqual(compute_simulation(LINEAR)["status"],
                         "COMPUTED_ASSUMPTIONS_UNVALIDATED")
        self.assertEqual(compute_simulation(LINEAR), compute_simulation(LINEAR))

    def test_auto_run_independent_simulations_and_chain_then_resume(self):
        plan = {"tasks": [
            task("simulation", "Évolution linéaire", simulation=LINEAR),
            task("simulation", "Décroissance", (0,), DECAY),
            task("hardware", "Mesurer les valeurs"),
            task("simulation", "Dépend de la mesure", (2,), LINEAR),
        ]}
        view = self.start(plan)
        self.assertEqual(view.status, "READY")
        self.assertEqual(view.workplan_state, "STAGED")
        self.assertEqual(view.queued_tasks, 0)
        self.assertEqual(view.simulated_tasks, 2)
        self.assertEqual(view.deferred_tasks, 2)
        self.assertEqual(len(self.store.missions()), 1)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM grants").fetchone()[0], 0)
        records = self.store.db.execute(
            "SELECT result_json FROM research_simulation_runs ORDER BY task_index"
        ).fetchall()
        self.assertEqual(len(records), 2)
        self.assertEqual(json.loads(records[0][0])["series"], [10, 13, 16, 19, 22])
        self.assertEqual(json.loads(records[1][0])["final"], 640)
        self.assertEqual(len([event for event in self.store.events()
                              if event["event"] == "RND_SIMULATION_COMPUTED"]), 2)
        with patch.object(ollama_protocol, "_http",
                          side_effect=AssertionError("should not query LLM")):
            resumed = self.chat.resume(view.session_id)
        self.assertEqual(resumed.simulated_tasks, 2)
        self.assertEqual(len([event for event in self.store.events()
                              if event["event"] == "RND_SIMULATION_COMPUTED"]), 2)
        with StateStore(self.db) as reopened:
            again = ProjectChatCoordinator(
                reopened, repository=REPO, config_path=self.config)
            self.assertEqual(again.latest().simulated_tasks, 2)

    def test_existing_workplans_without_parameters_remain_deferred(self):
        view = self.start({"tasks": [
            task("simulation", "Simulation non paramétrée"),
            task("research", "Rechercher la méthode"),
        ]})
        self.assertEqual(view.simulated_tasks, 0)
        self.assertEqual(view.deferred_tasks, 2)
        self.assertIsNone(self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE name='research_simulation_runs'"
        ).fetchone())

    def test_simulation_after_code_or_hardware_is_not_falsely_run(self):
        view = self.start({"tasks": [
            task("code", "Créer le programme"),
            task("simulation", "Besoin du code", (0,), LINEAR),
            task("hardware", "Mesure physique"),
            task("simulation", "Besoin du banc", (2,), DECAY),
        ]})
        self.assertEqual(view.queued_tasks, 1)
        self.assertEqual(view.deferred_tasks, 3)
        self.assertEqual(view.simulated_tasks, 0)

    def test_malformed_or_unbounded_simulation_is_rejected(self):
        for spec in (
            dict(LINEAR, steps=True),
            dict(LINEAR, steps=65),
            dict(LINEAR, parameter=10001),
            dict(DECAY, initial=-1),
            dict(DECAY, parameter=10001),
            dict(LINEAR, model="python"),
            dict(LINEAR, command="print('x')"),
            [1, 2, 3],
        ):
            with self.subTest(spec=spec), self.assertRaises(SimulationError):
                validate_simulation_spec(spec)
        from holding.research_workplan import _validate, WorkplanError
        with self.assertRaises(WorkplanError):
            _validate(json.dumps({"tasks": [
                task("code", "Unsafe", simulation=LINEAR)]}))
        with self.assertRaises(SimulationError):
            _validate(json.dumps({"tasks": [
                task("simulation", "Unsafe", simulation=dict(LINEAR, steps=10000))]}))

    def test_corrupted_result_is_detected_not_reported(self):
        view = self.start({"tasks": [
            task("simulation", "Calcul borné", simulation=LINEAR)]})
        with self.store._transaction():
            self.store.db.execute(
                "UPDATE research_simulation_runs SET result_json='{}'")
        with self.assertRaisesRegex(SimulationError, "integrity"):
            self.chat.view(view.session_id)
        with self.assertRaisesRegex(SimulationError, "integrity"):
            self.chat.resume(view.session_id)

    def test_paused_model_never_generates_or_computes(self):
        self.store.set_paused(True)
        with patch.object(ollama_protocol, "_http",
                          side_effect=AssertionError("No external model call")):
            with self.assertRaisesRegex(Exception, "paused"):
                self.chat.start("Tester un modèle numérique.")
        self.assertEqual(self.store.missions(), ())
        self.store.set_paused(False)

    def test_no_executable_code_from_model_parameters(self):
        view = self.start({"tasks": [
            task("simulation", "Simuler sans accès système", simulation=LINEAR)]})
        self.assertEqual(view.simulated_tasks, 1)
        self.assertEqual(self.store.missions()[0].operation, "plan")
        self.assertTrue(all(x["event"] != "MISSION_CLAIMED"
                            for x in self.store.events()))


if __name__ == "__main__":
    unittest.main()

"""Pinned ngspice RC integration: real process smoke optional; no LLM network."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from holding import ollama_protocol
from holding.ngspice_adapter import (
    CircuitSimulationError, _parse_trace, build_rc_deck,
    execute_rc, validate_circuit_spec, validate_pinned_engine,
)
from holding.project_chat import ProjectChatCoordinator
from holding.research_workplan import WorkplanError, _validate
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
MODEL_CONFIG = {
    "schema": 1, "model": "chat-test:Q4", "digest": "a" * 64,
    "num_ctx": 4096, "timeout": 5,
}
CIRCUIT = {
    "resistance_ohms": 1000,
    "capacitance_microfarads": 100,
    "supply_millivolts": 5000,
    "initial_millivolts": 0,
    "duration_ms": 1000,
    "samples": 32,
}
BRIEF = {
    "concept": "Étudier le comportement théorique d'un circuit RC.",
    "hypotheses": ["Le temps de réponse suit le produit RC."],
    "experiments": [{
        "name": "Simulation", "method": "Comparer un modèle transitoire.",
        "success_measure": "Tension simulée enregistrée avec unités.",
    }],
    "prototype_steps": ["Élaborer un circuit d'essai."],
    "unknowns": ["Les composants et leurs tolérances ne sont pas mesurés."],
}


def task(kind, title, deps=(), circuit=None):
    result = {
        "kind": kind, "title": title,
        "acceptance": ["Produire une trace vérifiable."],
        "depends_on": list(deps),
    }
    if circuit is not None:
        result["circuit"] = circuit
    return result


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NgspiceAdapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "holding.sqlite"
        self.model_config = self.root / "model.json"
        self.model_config.write_text(json.dumps(MODEL_CONFIG), encoding="utf-8")
        self.simulator_config = self.root / "ngspice.json"
        self.executable = Path(sys.executable).resolve()
        self.simulator_config.write_text(json.dumps({
            "schema": 1,
            "executable": str(self.executable),
            "sha256": sha_file(self.executable),
            "timeout_seconds": 5,
        }), encoding="utf-8")
        self.store = StateStore(self.db)
        self.addCleanup(self.store.close)

    def chat(self, *, configured=True):
        return ProjectChatCoordinator(
            self.store, repository=REPO, config_path=self.model_config,
            circuit_config_path=self.simulator_config if configured else None,
        )

    def create(self, workplan, *, configured=True, mock_simulator=True):
        messages = iter([
            {"message": {"content": '{"questions":[]}'}},
            {"message": {"content": json.dumps(BRIEF, ensure_ascii=False)}},
            {"message": {"content": json.dumps(workplan, ensure_ascii=False)}},
        ])

        def fake_ngspice(argv, **opts):
            self.assertEqual(argv[0], str(self.executable))
            self.assertEqual(argv[1:], ["-n", "-b", "-o", "simulator.log", "rc.cir"])
            self.assertFalse(opts.get("shell", False))
            deck = (Path(opts["cwd"]) / "rc.cir").read_text(encoding="utf-8")
            self.assertEqual(deck, build_rc_deck(CIRCUIT))
            self.assertNotIn(".include", deck.casefold())
            (Path(opts["cwd"]) / "trace.txt").write_text(
                "0 0\n0.5 4\n1.0 4.99\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        with patch.object(ollama_protocol, "_installed_model"), \
             patch.object(ollama_protocol, "_http",
                          side_effect=lambda *args, **kw: next(messages)), \
             patch("holding.ngspice_adapter.subprocess.run",
                   side_effect=fake_ngspice if mock_simulator
                   else AssertionError("Simulator must remain uninvoked")) as run:
            result = self.chat(configured=configured).start(
                "Concevoir et simuler un montage RC avant prototypage.")
        return result, run

    def test_exact_rc_numeric_contract_and_no_untrusted_netlist(self):
        deck = build_rc_deck(CIRCUIT)
        self.assertIn("Rfixed src out 1000", deck)
        self.assertIn("Cfixed out 0 100u IC=0", deck)
        self.assertIn("wrdata trace.txt v(out)", deck)
        self.assertNotIn(".include", deck.casefold())
        self.assertNotIn("shell", deck.casefold())
        self.assertEqual(validate_circuit_spec(CIRCUIT), CIRCUIT)
        self.assertEqual(validate_pinned_engine(self.simulator_config)[1],
                         sha_file(self.executable))

    def test_integrated_chat_executes_one_pinned_circuit_with_provenance(self):
        view, process = self.create({"tasks": [
            task("simulation", "Simuler RC", circuit=CIRCUIT),
        ]})
        self.assertEqual(view.status, "READY")
        self.assertEqual(view.workplan_state, "STAGED")
        self.assertEqual(view.deferred_tasks, 0)
        self.assertEqual(view.simulated_tasks, 0)
        self.assertEqual(len(view.circuit_results), 1)
        self.assertIn("4.99", view.circuit_results[0])
        process.assert_called_once()
        self.assertEqual(len(self.store.missions()), 1)
        self.assertEqual(self.store.missions()[0].operation, "plan")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM grants").fetchone()[0], 0)
        row = self.store.db.execute(
            "SELECT result_json FROM research_circuit_runs"
        ).fetchone()
        result = json.loads(row["result_json"])
        self.assertEqual(result["trace_seconds_volts"],
                         [[0.0, 0.0], [0.5, 4.0], [1.0, 4.99]])
        self.assertEqual(result["status"], "COMPUTED_ASSUMPTIONS_UNVALIDATED")
        with patch("holding.ngspice_adapter.subprocess.run",
                   side_effect=AssertionError("cache must prevent rerun")), \
             patch.object(ollama_protocol, "_http",
                          side_effect=AssertionError("LLM must not repeat")):
            again = self.chat().resume(view.session_id)
        self.assertEqual(again.circuit_results, view.circuit_results)
        self.assertEqual(len([e for e in self.store.events()
                              if e["event"] == "RND_CIRCUIT_COMPUTED"]), 1)

    def test_missing_operator_config_defers_without_invoking_any_executable(self):
        view, process = self.create({"tasks": [
            task("simulation", "Simuler RC", circuit=CIRCUIT),
        ]}, configured=False, mock_simulator=False)
        process.assert_not_called()
        self.assertEqual(view.deferred_tasks, 1)
        self.assertEqual(view.circuit_results, ())
        self.assertIsNone(self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE name='research_circuit_runs'"
        ).fetchone())
        with patch("holding.ngspice_adapter.subprocess.run",
                   return_value=subprocess.CompletedProcess(["mock"], 0)) as run:
            # Restart without config is always a no-op, never an implicit tool grant.
            self.assertEqual(self.chat(configured=False).resume(
                view.session_id).deferred_tasks, 1)
            run.assert_not_called()

    def test_hardware_dependency_is_not_simulated_even_if_ngspice_configured(self):
        view, process = self.create({"tasks": [
            task("hardware", "Mesurer réellement un condensateur"),
            task("simulation", "Utiliser la mesure non disponible",
                 deps=[0], circuit=CIRCUIT),
        ]}, mock_simulator=False)
        self.assertEqual(view.deferred_tasks, 2)
        self.assertEqual(view.circuit_results, ())
        process.assert_not_called()

    def test_alternating_simulators_release_code_once_with_result_provenance(self):
        def number(title, deps=()):
            return dict(task("simulation", title, deps=deps), simulation={
                "model": "linear_discrete", "initial": 1,
                "parameter": 2, "steps": 3})
        view, process = self.create({"tasks": [
            number("Premier calcul"),
            task("simulation", "RC suivant", deps=[0], circuit=CIRCUIT),
            number("Calcul après RC", deps=[1]),
            task("simulation", "Second RC", deps=[2], circuit=CIRCUIT),
            task("code", "Exploiter les résultats", deps=[3]),
            task("code", "Tester le module", deps=[4]),
            task("hardware", "Mesure réelle"),
            number("Calcul encore bloqué", deps=[6]),
        ]})
        self.assertEqual(process.call_count, 2)
        self.assertEqual((len(view.circuit_results), view.simulated_tasks), (2, 2))
        self.assertEqual((view.queued_tasks, view.deferred_tasks), (2, 2))
        missions = {m.id: m for m in self.store.missions()}
        first = f"build-{view.session_id[:24]}-5"
        second = f"build-{view.session_id[:24]}-6"
        self.assertEqual(missions[first].dependencies, ())
        self.assertEqual(missions[second].dependencies, (first,))
        result_sha = self.store.db.execute(
            "SELECT result_sha256 FROM research_circuit_runs WHERE task_index=3"
        ).fetchone()[0]
        spec = json.loads(self.store.db.execute(
            "SELECT spec_json FROM mission_specs WHERE id=?", (first,)
        ).fetchone()[0])
        self.assertIn(result_sha, " ".join(spec["acceptance"]))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM grants").fetchone()[0], 0)
        before = len(self.store.events())
        with patch("holding.ngspice_adapter.subprocess.run",
                   side_effect=AssertionError("no repeated simulation")), \
             patch.object(ollama_protocol, "_http",
                          side_effect=AssertionError("no regenerated plan")):
            for _ in range(2):
                resumed = self.chat().resume(view.session_id)
                self.assertEqual((resumed.queued_tasks, resumed.deferred_tasks), (2, 2))
        self.assertEqual(len(self.store.events()), before)

    def test_missing_simulator_then_configured_resume_releases_deferred_code(self):
        from holding.roadmap_replan import SafeRoadmapReplanner
        view, _ = self.create({"tasks": [
            task("simulation", "RC", circuit=CIRCUIT),
            task("code", "Exploiter RC", deps=[0]),
        ]}, configured=False, mock_simulator=False)
        self.assertEqual((view.queued_tasks, view.deferred_tasks), (0, 2))
        saved = SafeRoadmapReplanner.append_direction
        def after_commit(replanner, *args, **kwargs):
            saved(replanner, *args, **kwargs)
            raise TimeoutError("crash after deferred code was imported")
        def process(argv, **kwargs):
            (Path(kwargs["cwd"]) / "trace.txt").write_text("0 0\n1 5\n")
            return subprocess.CompletedProcess(argv, 0)
        with patch("holding.ngspice_adapter.subprocess.run", side_effect=process), \
             patch.object(SafeRoadmapReplanner, "append_direction", after_commit):
            with self.assertRaises(TimeoutError):
                self.chat().resume(view.session_id)
        self.assertEqual(len(self.store.missions()), 2)
        self.assertEqual(self.chat().view(view.session_id).queued_tasks, 0)
        with StateStore(self.db) as reopened:
            coordinator = ProjectChatCoordinator(
                reopened, repository=REPO, config_path=self.model_config,
                circuit_config_path=self.simulator_config)
            with patch("holding.ngspice_adapter.subprocess.run",
                       side_effect=AssertionError("already calculated")), \
                 patch.object(ollama_protocol, "_http",
                              side_effect=AssertionError("already planned")):
                resumed = coordinator.resume(view.session_id)
                self.assertEqual((resumed.queued_tasks, resumed.deferred_tasks), (1, 0))
                coordinator.resume(view.session_id)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM roadmap_amendments").fetchone()[0], 1)
        self.assertEqual(len([e for e in self.store.events()
                              if e["event"] == "RND_WORKPLAN_ADVANCED"]), 1)

    def test_corrupt_rc_result_cannot_release_deferred_code(self):
        view, _ = self.create({"tasks": [
            task("simulation", "RC", circuit=CIRCUIT),
            task("code", "Exploiter RC", deps=[0]),
        ]}, configured=False, mock_simulator=False)
        def process(argv, **kwargs):
            (Path(kwargs["cwd"]) / "trace.txt").write_text("0 0\n1 5\n")
            return subprocess.CompletedProcess(argv, 0)
        with patch("holding.ngspice_adapter.subprocess.run", side_effect=process), \
             patch("holding.research_workplan.SafeRoadmapReplanner.append_direction",
                   side_effect=TimeoutError("before import")):
            with self.assertRaises(TimeoutError):
                self.chat().resume(view.session_id)
        self.store.db.execute("UPDATE research_circuit_runs SET result_json='{}'")
        with self.assertRaises(CircuitSimulationError):
            self.chat().resume(view.session_id)
        self.assertEqual(len(self.store.missions()), 1)

    def test_failed_rc_does_not_block_independent_code_or_repeat_within_a_pass(self):
        number = dict(task("simulation", "Calcul indépendant"), simulation={
            "model": "linear_discrete", "initial": 1, "parameter": 2, "steps": 3})
        plan = {"tasks": [
            number,
            task("code", "Exploiter le calcul", deps=[0]),
            task("simulation", "RC indisponible", circuit=CIRCUIT),
            task("code", "Ne pas exploiter le RC absent", deps=[2]),
        ]}
        with patch("holding.ngspice_adapter.execute_rc",
                   side_effect=CircuitSimulationError("engine unavailable")) as execute:
            view, _ = self.create(plan)
        execute.assert_called_once()
        self.assertEqual((view.queued_tasks, view.deferred_tasks), (1, 2))
        self.assertIn("simulation RC en échec", view.task_details[2])
        self.assertEqual(len(self.store.missions()), 2)
        self.assertEqual(len([e for e in self.store.events()
                              if e["event"] == "RND_CIRCUIT_DEFERRED"]), 1)
        with patch("holding.ngspice_adapter.execute_rc",
                   side_effect=CircuitSimulationError("engine unavailable")) as execute:
            self.chat().resume(view.session_id)
        execute.assert_called_once()
        self.assertEqual(len([e for e in self.store.events()
                              if e["event"] == "RND_CIRCUIT_DEFERRED"]), 1)
        def success(argv, **kwargs):
            (Path(kwargs["cwd"]) / "trace.txt").write_text("0 0\n1 5\n")
            return subprocess.CompletedProcess(argv, 0)
        with patch("holding.ngspice_adapter.subprocess.run", side_effect=success):
            recovered = self.chat().resume(view.session_id)
        self.assertEqual((recovered.queued_tasks, recovered.deferred_tasks), (2, 0))
        self.assertNotIn("simulation RC en échec", recovered.task_details[2])

    def test_invalid_or_injected_circuit_never_reaches_simulator(self):
        from holding.ngspice_adapter import validate_circuit_spec
        values = [
            dict(CIRCUIT, samples=True),
            dict(CIRCUIT, samples=200000),
            dict(CIRCUIT, resistance_ohms=0),
            dict(CIRCUIT, supply_millivolts=100001),
            dict(CIRCUIT, file="/tmp/attack"),
            dict(CIRCUIT, capacitance_microfarads=0),
            "V1 1 0 1000\n.shell ...",
        ]
        for value in values:
            with self.subTest(value=str(value)[:60]):
                with self.assertRaises(CircuitSimulationError):
                    validate_circuit_spec(value)
        with self.assertRaises(WorkplanError):
            _validate(json.dumps({"tasks": [
                task("code", "Unsafe circuit", circuit=CIRCUIT)]}))
        with self.assertRaises(WorkplanError):
            _validate(json.dumps({"tasks": [
                dict(task("simulation", "Mixed", circuit=CIRCUIT),
                     simulation={"model": "linear_discrete",
                                 "initial": 1, "parameter": 1, "steps": 2})]}))

    def test_operator_checksum_change_fails_closed(self):
        bad = json.loads(self.simulator_config.read_text(encoding="utf-8"))
        bad["sha256"] = "0" * 64
        self.simulator_config.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaisesRegex(CircuitSimulationError, "checksum mismatch"):
            execute_rc(CIRCUIT, self.simulator_config)
        self.assertIsNone(self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE name='research_circuit_runs'"
        ).fetchone())

    def test_trace_rejects_corruption_and_nonfinite_values(self):
        trace = self.root / "trace.txt"
        for content in ("", "bad\n", "0 0\n0 nan\n",
                        "0 0\n-1 2\n", "0 0\n0.5 2\n0.2 1\n",
                        "0 0\n1 2 3\n", "0 0\n2 1\n"):
            with self.subTest(content=content):
                trace.write_text(content, encoding="utf-8")
                with self.assertRaises(CircuitSimulationError):
                    _parse_trace(trace, 1.0)

    def test_changed_persisted_output_never_reported_as_valid(self):
        v, _ = self.create({"tasks": [
            task("simulation", "Simuler RC", circuit=CIRCUIT)]})
        with self.store._transaction():
            self.store.db.execute(
                "UPDATE research_circuit_runs SET result_json='{}'")
        with self.assertRaises(CircuitSimulationError):
            self.chat().view(v.session_id)
        with self.assertRaises(CircuitSimulationError):
            self.chat().resume(v.session_id)

    def test_pause_during_native_simulation_prevents_persistence(self):
        def suspend(argv, **kwargs):
            self.store.set_paused(True)
            (Path(kwargs["cwd"]) / "trace.txt").write_text(
                "0 0\n0.5 4\n1 5\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0)
        messages = iter([
            {"message": {"content": '{"questions":[]}'}},
            {"message": {"content": json.dumps(BRIEF, ensure_ascii=False)}},
            {"message": {"content": json.dumps({"tasks": [
                task("simulation", "RC", circuit=CIRCUIT)]})}},
        ])
        with patch.object(ollama_protocol, "_installed_model"), \
             patch.object(ollama_protocol, "_http",
                          side_effect=lambda *a, **kw: next(messages)), \
             patch("holding.ngspice_adapter.subprocess.run", side_effect=suspend):
            with self.assertRaisesRegex(CircuitSimulationError, "paused"):
                self.chat().start("Tester un RC en différé.")
        self.assertIsNone(self.store.db.execute(
            "SELECT name FROM sqlite_master WHERE name='research_circuit_runs'"
        ).fetchone())

    @unittest.skipUnless(os.environ.get("ATLAS_TEST_REAL_NGSPICE"),
                         "Real ngspice only in dedicated opt-in CI")
    def test_real_pinned_ngspice_smoke(self):
        exe = Path(os.environ["ATLAS_TEST_REAL_NGSPICE"]).resolve()
        if not exe.is_file():
            self.fail("Dedicated ngspice smoke requires an installed simulator")
        real = self.root / "real-ngspice.json"
        real.write_text(json.dumps({
            "schema": 1,
            "executable": str(exe),
            "sha256": sha_file(exe),
            "timeout_seconds": 20,
        }), encoding="utf-8")
        result, exe_sha = execute_rc(CIRCUIT, real)
        self.assertEqual(exe_sha, sha_file(exe))
        self.assertEqual(result["engine"], "ngspice_rc_transient")
        self.assertGreater(len(result["trace_seconds_volts"]), 2)
        self.assertGreater(result["final_voltage_volts"], 4.5)
        self.assertLess(result["final_voltage_volts"], 5.1)


if __name__ == "__main__":
    unittest.main()

"""Real circuit trace vs operator-supplied measurements, offline tests."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from holding import ollama_protocol
from holding.measurement_cli import main as measurement_main
from holding.project_chat import ProjectChatCoordinator
from holding.research_measurement import (
    MeasurementComparisonError, MeasurementComparisonService,
    STATUS, compare_to_simulation, parse_measurements,
)
from holding.state_store import StateStore

REPO = "r9327/Dof-Atlas"
CIRCUIT = {
    "resistance_ohms": 1000, "capacitance_microfarads": 100,
    "supply_millivolts": 5000, "initial_millivolts": 0,
    "duration_ms": 1000, "samples": 32,
}
CONFIG = {"schema":1,"model":"local-test:Q4","digest":"a"*64,
          "num_ctx":4096,"timeout":5}
BRIEF = {
    "concept":"Contrôler un prototype RC par comparaison.",
    "hypotheses":["Les mesures peuvent différer des prédictions."],
    "experiments":[{"name":"Comparer RC","method":"Importer deux séries horodatées.",
                    "success_measure":"Calculer RMSE et erreur maximale."}],
    "prototype_steps":["Mesurer le circuit."],
    "unknowns":["Les valeurs des composants ne sont pas étalonnées."],
}
PLAN = {"tasks":[{
    "kind":"simulation","title":"Simuler circuit RC",
    "acceptance":["Sauvegarder une trace."],"depends_on":[],
    "circuit":CIRCUIT,
}]}
CSV = (b"time_seconds,voltage_volts\n"
       b"0,0.1\n0.25,2.1\n0.5,3.9\n1,5\n")


class MeasurementComparisonTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root/"holding.sqlite"
        self.config = self.root/"model.json"
        self.config.write_text(json.dumps(CONFIG),encoding="utf-8")
        exe=Path(sys.executable).resolve()
        self.circuit_config=self.root/"ngspice.json"
        self.circuit_config.write_text(json.dumps({
            "schema":1, "executable":str(exe),
            "sha256":hashlib.sha256(exe.read_bytes()).hexdigest(),
            "timeout_seconds":5,
        }),encoding="utf-8")
        self.store=StateStore(self.db)
        self.addCleanup(self.store.close)
        self.chat=ProjectChatCoordinator(self.store,repository=REPO,
            config_path=self.config,circuit_config_path=self.circuit_config)
        self.compare=MeasurementComparisonService(self.store,repository=REPO)

    def create_simulated_project(self):
        responses=iter([
            {"message":{"content":'{"questions":[]}'}},
            {"message":{"content":json.dumps(BRIEF,ensure_ascii=False)}},
            {"message":{"content":json.dumps(PLAN,ensure_ascii=False)}},
        ])
        def native(argv,**kwargs):
            self.assertEqual(argv[1:],["-n","-b","-o","simulator.log","rc.cir"])
            (Path(kwargs["cwd"])/"trace.txt").write_text(
                "0 0\n0.5 4\n1 4.99\n",encoding="utf-8")
            return subprocess.CompletedProcess(argv,0,b"",b"")
        with patch.object(ollama_protocol,"_installed_model"), \
             patch.object(ollama_protocol,"_http",
                          side_effect=lambda *a,**k:next(responses)), \
             patch("holding.ngspice_adapter.subprocess.run",side_effect=native):
            return self.chat.start("Concevoir et comparer un circuit RC prototype.")

    def test_import_and_compare_with_interpolation_and_provenance(self):
        session=self.create_simulated_project()
        self.assertEqual(session.circuit_task_indices,(0,))
        receipt=self.compare.import_csv(session.session_id,0,CSV)
        self.assertEqual(receipt.status,STATUS)
        self.assertFalse(receipt.cached)
        self.assertEqual(receipt.samples,4)
        self.assertAlmostEqual(receipt.max_abs_volts,0.1)
        self.assertGreater(receipt.rmse_volts,0.08)
        self.assertLess(receipt.rmse_volts,0.10)
        self.assertEqual(receipt.source_sha256,hashlib.sha256(CSV).hexdigest())
        self.assertEqual(len(receipt.comparison_sha256),64)
        self.assertEqual(len(self.store.missions()),1)
        self.assertEqual(self.store.missions()[0].state,"READY")
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM grants").fetchone()[0],0)
        row=self.store.db.execute(
            "SELECT comparison_json FROM research_measurement_comparisons"
        ).fetchone()
        comparison=json.loads(row["comparison_json"])
        self.assertEqual(comparison["aligned_time_measured_simulated"][1],
                         [0.25,2.1,2.0])
        self.assertIn("non vérifiés",comparison["note"])
        view=self.chat.view(session.session_id)
        self.assertEqual(len(view.comparison_results),1)
        self.assertIn("RMSE",view.comparison_results[0])
        self.assertIn("mesures non certifiées",view.comparison_results[0])
        self.assertIn("Tâche 1",view.comparison_results[0])
        again=self.compare.import_csv(session.session_id,0,CSV)
        self.assertTrue(again.cached)
        self.assertEqual(again.comparison_sha256,receipt.comparison_sha256)
        self.assertEqual(len([e for e in self.store.events()
                              if e["event"]=="RND_MEASUREMENTS_COMPARED"]),1)
        with StateStore(self.db) as store:
            retrieved=MeasurementComparisonService(
                store,repository=REPO).inspect(session.session_id)
        self.assertEqual(len(retrieved),1)
        self.assertEqual(retrieved[0].rmse_volts,receipt.rmse_volts)

    def test_cli_imports_only_operator_selected_file_then_cached(self):
        session=self.create_simulated_project()
        csv_path=self.root/"measurements.csv"
        csv_path.write_bytes(CSV)
        argv=["--db",str(self.db),"--repo",REPO,
              "--session-id",session.session_id,"--task-index","0",
              "--csv-file",str(csv_path)]
        for cached in (False,True):
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(measurement_main(argv),0)
            response=json.loads(out.getvalue())
            self.assertEqual(response["cached"],cached)
            self.assertEqual(response["status"],STATUS)

    def test_invalid_csv_is_rejected_and_does_not_write(self):
        session=self.create_simulated_project()
        bad=(
            b"time,voltage\n0,0\n1,5\n",
            b"time_seconds,voltage_volts\n0,0\n",
            b"time_seconds,voltage_volts\n0,0\n0,1\n",
            b"time_seconds,voltage_volts\n1,0\n0,5\n",
            b"time_seconds,voltage_volts\n0,nan\n1,5\n",
            b"time_seconds,voltage_volts\n0,inf\n1,5\n",
            b"time_seconds,voltage_volts\n0,0\n1,2000\n",
            b"time_seconds,voltage_volts\n0,0\n2,5\n",
            b"time_seconds,voltage_volts\n0,0\n=HYPERLINK('x'),5\n",
            b"time_seconds,voltage_volts\n0,0\n1,5,6\n",
            b"\xff\xfe",
            CSV*40000,
        )
        for content in bad:
            with self.subTest(raw=content[:55]):
                with self.assertRaises(MeasurementComparisonError):
                    self.compare.import_csv(session.session_id,0,content)
        self.assertEqual(self.compare.inspect(session.session_id),())

    def test_outside_simulation_range_and_zero_sample_extrapolation_refused(self):
        trace=[[0.1,0.5],[0.5,2.0],[1.0,4.0]]
        for data in (
            [[0.0,0.0],[0.5,2.0]],
            [[0.1,0.5],[1.5,5.0]],
            [[0.5,2],[0.5,2]],
        ):
            with self.subTest(data=data),self.assertRaises(MeasurementComparisonError):
                compare_to_simulation(trace,data)

    def test_wrong_session_task_and_repository_rejected(self):
        session=self.create_simulated_project()
        for sid,idx in (("0"*32,0),(session.session_id,-1),
                        (session.session_id,1),(session.session_id,True)):
            with self.subTest(id=sid,index=idx),self.assertRaises(MeasurementComparisonError):
                self.compare.import_csv(sid,idx,CSV)
        with self.assertRaises(MeasurementComparisonError):
            MeasurementComparisonService(
                self.store,repository="not/this-repo").import_csv(
                session.session_id,0,CSV)
        self.assertFalse(self.compare.inspect(session.session_id))

    def test_tampered_stored_comparison_never_reappears_as_valid(self):
        session=self.create_simulated_project()
        self.compare.import_csv(session.session_id,0,CSV)
        with self.store._transaction():
            self.store.db.execute(
                "UPDATE research_measurement_comparisons "
                "SET comparison_json='{}'")
        with self.assertRaisesRegex(MeasurementComparisonError,"integrity"):
            self.compare.inspect(session.session_id)
        with self.assertRaises(MeasurementComparisonError):
            self.chat.view(session.session_id)

    def test_tampered_measurement_but_updated_hash_still_detected(self):
        session=self.create_simulated_project()
        self.compare.import_csv(session.session_id,0,CSV)
        changed=json.dumps([[0,0],[1,0]],separators=(",",":"))
        with self.store._transaction():
            self.store.db.execute(
                "UPDATE research_measurement_comparisons "
                "SET observations_json=?,observations_sha256=?",
                (changed,hashlib.sha256(changed.encode()).hexdigest()))
        with self.assertRaisesRegex(MeasurementComparisonError,"calculation mismatch"):
            self.compare.inspect(session.session_id)

    def test_compare_requires_stored_simulation_and_real_data_provenance(self):
        session=self.create_simulated_project()
        with self.store._transaction():
            self.store.db.execute(
                "DELETE FROM research_circuit_runs WHERE session_id=?",
                (session.session_id,))
        with self.assertRaises(MeasurementComparisonError):
            self.compare.import_csv(session.session_id,0,CSV)

    def test_pause_blocks_import_and_does_not_claim_measurement(self):
        session=self.create_simulated_project()
        self.store.set_paused(True)
        with self.assertRaisesRegex(MeasurementComparisonError,"paused"):
            self.compare.import_csv(session.session_id,0,CSV)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM grants").fetchone()[0],0)

    def test_source_changes_are_separate_comparisons_not_rewrites(self):
        session=self.create_simulated_project()
        first=self.compare.import_csv(session.session_id,0,CSV)
        second=self.compare.import_csv(
            session.session_id,0,CSV.replace(b"0.25,2.1",b"0.25,2.2"))
        self.assertNotEqual(first.source_sha256,second.source_sha256)
        self.assertEqual(len(self.compare.inspect(session.session_id)),2)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM research_measurement_comparisons").fetchone()[0],2)

    def test_import_file_fails_on_relative_or_missing_files(self):
        session=self.create_simulated_project()
        for path in (Path("measurements.csv"),self.root/"missing.csv"):
            with self.subTest(path=str(path)),self.assertRaises(MeasurementComparisonError):
                self.compare.import_file(session.session_id,0,path)

    def test_cli_does_not_print_tracebacks_or_claim_success_for_bad_file(self):
        with redirect_stderr(io.StringIO()) as err, \
             self.assertRaises(SystemExit) as caught:
            measurement_main([
                "--db",str(self.db),"--repo",REPO,"--session-id","0"*32,
                "--task-index","0","--csv-file",str(self.root/"absent.csv"),
            ])
        self.assertEqual(caught.exception.code,2)
        self.assertNotIn("Traceback",err.getvalue())


if __name__=="__main__":
    unittest.main()

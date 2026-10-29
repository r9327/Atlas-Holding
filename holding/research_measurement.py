"""Compare imported RC voltage observations with an actual ngspice trace.

Measurements are operator-supplied, NOT independently verified or taken from
hardware by the Holding. Comparison is a reproducible analysis, not proof that
a prototype works. No LLM, shell, network or device access is used here.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
from typing import Any

from .ngspice_adapter import CircuitSimulationRunner
from .research_workplan import WorkplanView, _validate as validate_workplan
from .state_store import StateStore
from .schema import RESEARCH_MEASUREMENT_SCHEMA, apply_schema

MAX_CSV_BYTES = 64 * 1024
MAX_POINTS = 512
STATUS = "COMPARISON_MEASUREMENTS_UNVERIFIED"
_HEADER = ["time_seconds", "voltage_volts"]


class MeasurementComparisonError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def parse_measurements(raw: bytes) -> list[list[float]]:
    """Strict two-column UTF-8 CSV with explicit SI units and no free text."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_CSV_BYTES:
        raise MeasurementComparisonError("CSV missing or exceeds 64 KiB")
    try:
        decoded = raw.decode("utf-8-sig")
        rows = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        if next(rows, None) != _HEADER:
            raise MeasurementComparisonError(
                "Expected CSV columns time_seconds,voltage_volts")
        readings: list[list[float]] = []
        previous = -1.0
        for row in rows:
            if len(row) != 2:
                raise MeasurementComparisonError("Invalid measurement row shape")
            try:
                t, value = float(row[0]), float(row[1])
            except ValueError as exc:
                raise MeasurementComparisonError("Non-numeric measurement row") from exc
            if (not math.isfinite(t) or not math.isfinite(value)
                    or not previous < t or t < 0 or t > 10.0
                    or abs(value) > 1000.0):
                raise MeasurementComparisonError("Invalid monotonic time or voltage")
            readings.append([t, value])
            previous = t
            if len(readings) > MAX_POINTS:
                raise MeasurementComparisonError("Too many measurement samples")
    except (UnicodeError, csv.Error, OverflowError) as exc:
        raise MeasurementComparisonError("Malformed UTF-8 CSV") from exc
    if len(readings) < 2:
        raise MeasurementComparisonError("At least two measurements are required")
    return readings


def compare_to_simulation(trace: list, measurements: list) -> dict:
    """Piecewise-linear simulation interpolation at measurement timestamps."""
    if (not isinstance(trace, list) or len(trace) < 2
            or not isinstance(measurements, list) or len(measurements) < 2):
        raise MeasurementComparisonError("Missing trace or measurements")
    times = []
    voltages = []
    last = -1.0
    for pair in trace:
        if (not isinstance(pair, list) or len(pair) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in pair)
                or pair[0] <= last or pair[0] < 0 or pair[0] > 10
                or abs(pair[1]) > 1000):
            raise MeasurementComparisonError("Invalid stored simulated trace")
        times.append(float(pair[0]))
        voltages.append(float(pair[1]))
        last = pair[0]
    errors = []
    aligned = []
    last_observed = -1.0
    for pair in measurements:
        if (not isinstance(pair, list) or len(pair) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in pair)):
            raise MeasurementComparisonError("Invalid observations")
        t, obs = pair
        if (t <= last_observed or t < times[0] or t > times[-1]
                or abs(obs) > 1000):
            raise MeasurementComparisonError(
                "Measurements must be ordered and inside simulated time range")
        last_observed = t
        pos = bisect_left(times, t)
        if pos < len(times) and times[pos] == t:
            predicted = voltages[pos]
        elif pos == 0 or pos == len(times):
            raise MeasurementComparisonError("Measurement time outside simulated trace")
        else:
            fraction = (t - times[pos - 1]) / (times[pos] - times[pos - 1])
            predicted = voltages[pos - 1] + fraction * (
                voltages[pos] - voltages[pos - 1])
        error = obs - predicted
        if not math.isfinite(error):
            raise MeasurementComparisonError("Nonfinite comparison result")
        errors.append(error)
        aligned.append([round(t, 12), round(obs, 9), round(predicted, 9)])
    count = len(errors)
    return {
        "status": STATUS,
        "samples": count,
        "time_start_seconds": measurements[0][0],
        "time_end_seconds": measurements[-1][0],
        "mae_volts": round(sum(abs(x) for x in errors) / count, 9),
        "rmse_volts": round(math.sqrt(sum(x * x for x in errors) / count), 9),
        "max_abs_volts": round(max(abs(x) for x in errors), 9),
        "bias_volts": round(sum(errors) / count, 9),
        "aligned_time_measured_simulated": aligned,
        "note": "Mesures fournies par un opérateur, authenticité et étalonnage non vérifiés.",
    }


@dataclass(frozen=True)
class MeasurementComparisonReceipt:
    session_id: str
    task_index: int
    source_sha256: str
    simulation_sha256: str
    comparison_sha256: str
    samples: int
    rmse_volts: float
    max_abs_volts: float
    status: str
    created_epoch: int
    cached: bool


class MeasurementComparisonService:
    def __init__(self, store: StateStore, *, repository: str):
        if not isinstance(store, StateStore):
            raise MeasurementComparisonError("Trusted state store required")
        if (not isinstance(repository, str)
                or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
                                repository) is None):
            raise MeasurementComparisonError("Exact repository required")
        self.store = store
        self.repository = repository

    def _source(self, session_id: str, task_index: int):
        if (not isinstance(session_id, str)
                or re.fullmatch(r"[0-9a-f]{32}", session_id) is None
                or type(task_index) is not int or not 0 <= task_index < 8):
            raise MeasurementComparisonError("Invalid session or circuit index")
        session = self.store.db.execute(
            "SELECT status,repository FROM project_chat_sessions "
            "WHERE session_id=?", (session_id,)).fetchone()
        if (session is None or session["repository"] != self.repository
                or session["status"] != "READY"):
            raise MeasurementComparisonError("Completed, selected project required")
        row = self.store.db.execute(
            "SELECT * FROM project_workplans WHERE session_id=? AND repository=?",
            (session_id, self.repository)).fetchone()
        if row is None or row["state"] != "STAGED":
            raise MeasurementComparisonError("Staged workplan required")
        if _hash_text(row["plan_json"]) != row["plan_sha256"]:
            raise MeasurementComparisonError("Workplan digest mismatch")
        try:
            data = validate_workplan(row["plan_json"])
        except ValueError as exc:
            raise MeasurementComparisonError("Invalid stored workplan") from exc
        if task_index >= len(data["tasks"]) or "circuit" not in data["tasks"][task_index]:
            raise MeasurementComparisonError("Selected task has no circuit simulation")
        plan = WorkplanView(
            session_id, row["state"], row["plan_sha256"], (), (),
            tuple(data["tasks"]))
        found = next(
            (r for r in CircuitSimulationRunner(
                self.store, config_path=None).inspect(plan)
             if r.task_index == task_index), None)
        if found is None:
            raise MeasurementComparisonError("Completed native RC simulation required")
        circuit = self.store.db.execute(
            "SELECT result_json,result_sha256 FROM research_circuit_runs "
            "WHERE session_id=? AND task_index=?",
            (session_id, task_index)).fetchone()
        if circuit is None or circuit["result_sha256"] != found.result_sha256:
            raise MeasurementComparisonError("Circuit source changed")
        try:
            result = json.loads(circuit["result_json"])
            trace = result["trace_seconds_volts"]
        except (ValueError, TypeError, KeyError) as exc:
            raise MeasurementComparisonError("Corrupt circuit trace") from exc
        return plan.plan_sha256, found.result_sha256, trace

    @staticmethod
    def _receipt(row, *, cached: bool) -> MeasurementComparisonReceipt:
        comparison = json.loads(row["comparison_json"])
        return MeasurementComparisonReceipt(
            row["session_id"], row["task_index"], row["source_sha256"],
            row["simulation_sha256"], row["comparison_sha256"],
            comparison["samples"], comparison["rmse_volts"],
            comparison["max_abs_volts"], STATUS, row["created_epoch"], cached,
        )

    def inspect(self, session_id: str, *, task_index: int | None = None
                ) -> tuple[MeasurementComparisonReceipt, ...]:
        exists = self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='research_measurement_comparisons'").fetchone()
        if not exists:
            return ()
        if not isinstance(session_id, str) or re.fullmatch(r"[0-9a-f]{32}", session_id) is None:
            raise MeasurementComparisonError("Invalid project session ID")
        query = ("SELECT * FROM research_measurement_comparisons "
                 "WHERE session_id=? ORDER BY created_epoch,task_index,source_sha256")
        records = self.store.db.execute(query, (session_id,)).fetchall()
        receipts = []
        source_cache = {}
        for row in records:
            index = row["task_index"]
            if task_index is not None and index != task_index:
                continue
            if index not in source_cache:
                source_cache[index] = self._source(session_id, index)
            plan_sha, sim_sha, trace = source_cache[index]
            if (row["plan_sha256"] != plan_sha or row["simulation_sha256"] != sim_sha
                    or _hash_text(row["observations_json"]) != row["observations_sha256"]
                    or _hash_text(row["comparison_json"]) != row["comparison_sha256"]):
                raise MeasurementComparisonError("Stored comparison integrity mismatch")
            try:
                observations = json.loads(row["observations_json"])
                generated = compare_to_simulation(trace, observations)
            except (ValueError, TypeError) as exc:
                raise MeasurementComparisonError("Invalid stored measurement record") from exc
            if row["comparison_json"] != _canonical(generated):
                raise MeasurementComparisonError("Stored comparison calculation mismatch")
            receipts.append(self._receipt(row, cached=True))
        return tuple(receipts)

    def import_csv(self, session_id: str, task_index: int, raw: bytes
                   ) -> MeasurementComparisonReceipt:
        if self.store.background_suspended():
            raise MeasurementComparisonError("Measurement import paused or stopped")
        observations = parse_measurements(raw)
        source_sha = _hash_bytes(raw)
        plan_sha, sim_sha, trace = self._source(session_id, task_index)
        # All observations must be within available simulated time; no
        # extrapolation, timestamp guessing, or fabricated hardware readings.
        result = compare_to_simulation(trace, observations)
        observations_json = _canonical(observations)
        comparison_json = _canonical(result)
        with self.store._transaction():
            if self.store.background_suspended():
                raise MeasurementComparisonError("Comparison interrupted")
            current_plan, current_sim, _ = self._source(session_id, task_index)
            if (current_plan, current_sim) != (plan_sha, sim_sha):
                raise MeasurementComparisonError("Circuit changed during comparison")
            apply_schema(self.store.db, RESEARCH_MEASUREMENT_SCHEMA)
            previous = self.store.db.execute(
                "SELECT * FROM research_measurement_comparisons "
                "WHERE session_id=? AND task_index=? AND source_sha256=?",
                (session_id, task_index, source_sha)).fetchone()
            if previous is not None:
                if (previous["plan_sha256"] != plan_sha
                        or previous["simulation_sha256"] != sim_sha
                        or previous["observations_json"] != observations_json
                        or previous["comparison_json"] != comparison_json):
                    raise MeasurementComparisonError("Previous measurements conflict")
                # Verify the cached record against its original simulation.
                self.inspect(session_id, task_index=task_index)
                return self._receipt(previous, cached=True)
            if self.store.db.execute(
                    "SELECT COUNT(*) FROM research_measurement_comparisons "
                    "WHERE session_id=? AND task_index=?",
                    (session_id, task_index)).fetchone()[0] >= 16:
                raise MeasurementComparisonError(
                    "Maximum 16 measurement sets per circuit")
            when = int(time.time())
            self.store.db.execute(
                "INSERT INTO research_measurement_comparisons "
                "(session_id,task_index,plan_sha256,simulation_sha256,"
                "source_sha256,observations_sha256,observations_json,"
                "comparison_sha256,comparison_json,created_epoch)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (session_id, task_index, plan_sha, sim_sha,
                 source_sha, _hash_text(observations_json), observations_json,
                 _hash_text(comparison_json), comparison_json, when))
            self.store._event(
                None, "RND_MEASUREMENTS_COMPARED",
                f"session:{session_id} task:{task_index} "
                f"measurement_sha256:{source_sha[:16]} "
                f"comparison_sha256:{_hash_text(comparison_json)[:16]}")
        return MeasurementComparisonReceipt(
            session_id, task_index, source_sha, sim_sha,
            _hash_text(comparison_json), result["samples"], result["rmse_volts"],
            result["max_abs_volts"], STATUS, when, False,
        )

    def import_file(self, session_id: str, task_index: int,
                    file_path: Path) -> MeasurementComparisonReceipt:
        if (not isinstance(file_path, Path) or not file_path.is_absolute()
                or file_path.is_symlink() or not file_path.is_file()):
            raise MeasurementComparisonError("Existing absolute non-symlinked CSV required")
        try:
            with file_path.open("rb") as stream:
                raw = stream.read(MAX_CSV_BYTES + 1)
        except OSError as exc:
            raise MeasurementComparisonError("Cannot read measurements CSV") from exc
        return self.import_csv(session_id, task_index, raw)

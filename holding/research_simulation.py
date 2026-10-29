"""Trusted local simulation of bounded, hypothetical numerical models.

No model-generated code, shell, network, hardware, or scientific certification.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from .state_store import StateStore
from .schema import RESEARCH_SIMULATION_SCHEMA, apply_schema



class SimulationError(ValueError):
    pass


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_simulation_spec(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {
            "model", "initial", "parameter", "steps"}:
        raise SimulationError("Simulation requires exact model and integer parameters")
    model = value["model"]
    initial, param, steps = (value["initial"], value["parameter"], value["steps"])
    if model not in ("linear_discrete", "decay_discrete"):
        raise SimulationError("Only trusted numeric templates are allowed")
    if (any(type(v) is not int for v in (initial, param, steps))
            or not 1 <= steps <= 64 or not -1000000 <= initial <= 1000000):
        raise SimulationError("Out-of-range simulation input or steps")
    if model == "linear_discrete" and not -10000 <= param <= 10000:
        raise SimulationError("Invalid linear increment")
    if model == "decay_discrete" and not (
            0 <= initial <= 1000000 and 0 <= param <= 10000):
        raise SimulationError("Invalid decay retention")
    return value


def compute_simulation(value: Any) -> dict:
    """Calculate an integer series on hypothetical inputs, not measurements."""
    p = validate_simulation_spec(value)
    state = p["initial"]
    series = [state]
    for _ in range(p["steps"]):
        if p["model"] == "linear_discrete":
            state += p["parameter"]
        else:
            state = (state * p["parameter"]) // 10000
        series.append(state)
    return {
        "status": "COMPUTED_ASSUMPTIONS_UNVALIDATED",
        "model": p["model"], "inputs": p, "series": series,
        "final": state, "minimum": min(series), "maximum": max(series),
        "note": "Calcul sur hypothèses ; aucune mesure ou validation physique.",
    }


@dataclass(frozen=True)
class SimulationReceipt:
    session_id: str
    task_index: int
    result_sha256: str
    model: str
    final: int
    computed_epoch: int


class LocalSimulationRunner:
    def __init__(self, store: StateStore):
        if not isinstance(store, StateStore):
            raise SimulationError("Trusted state store required")
        self.store = store

    def inspect(self, plan) -> tuple[SimulationReceipt, ...]:
        """Inspect only actual, integrity-checked stored simulations."""
        exists = self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='research_simulation_runs'").fetchone()
        if exists is None:
            return ()
        rows = self.store.db.execute(
            "SELECT * FROM research_simulation_runs WHERE session_id=? "
            "ORDER BY task_index", (plan.session_id,),
        ).fetchall()
        result = []
        for row in rows:
            index = row["task_index"]
            if not 0 <= index < len(plan.tasks):
                raise SimulationError("Stored simulation task index invalid")
            task = plan.tasks[index]
            spec = task.get("simulation")
            if (task["kind"] != "simulation" or spec is None
                    or row["plan_sha256"] != plan.plan_sha256
                    or row["spec_sha256"] != _hash(_canonical(spec))):
                raise SimulationError("Simulation belongs to another plan or spec")
            computed = compute_simulation(spec)
            if (_hash(row["result_json"]) != row["result_sha256"]
                    or row["result_json"] != _canonical(computed)):
                raise SimulationError("Stored simulation integrity mismatch")
            result.append(SimulationReceipt(
                plan.session_id, index, row["result_sha256"],
                computed["model"], computed["final"], row["computed_epoch"]))
        return tuple(result)

    def run_available(self, plan) -> tuple[SimulationReceipt, ...]:
        """Run only bounded simulations whose dependencies already ran here."""
        if self.store.background_suspended():
            raise SimulationError("Simulations paused or stopped")
        if plan.state != "STAGED":
            raise SimulationError("Staged project workplan required")
        complete = {item.task_index for item in self.inspect(plan)}
        for index, task in enumerate(plan.tasks):
            spec = task.get("simulation")
            if (index in complete or task["kind"] != "simulation" or spec is None
                    or any(dep not in complete for dep in task["depends_on"])):
                continue
            text = _canonical(compute_simulation(spec))
            with self.store._transaction():
                if self.store.background_suspended():
                    raise SimulationError("Simulations paused or stopped")
                current = self.store.db.execute(
                    "SELECT plan_sha256,state FROM project_workplans "
                    "WHERE session_id=?", (plan.session_id,)).fetchone()
                if (current is None or current["plan_sha256"] != plan.plan_sha256
                        or current["state"] != "STAGED"):
                    raise SimulationError("Plan changed during simulation")
                apply_schema(self.store.db, RESEARCH_SIMULATION_SCHEMA)
                inserted = self.store.db.execute(
                    "INSERT OR IGNORE INTO research_simulation_runs "
                    "(session_id,task_index,plan_sha256,spec_sha256,"
                    "result_sha256,result_json,computed_epoch) VALUES(?,?,?,?,?,?,?)",
                    (plan.session_id, index, plan.plan_sha256,
                     _hash(_canonical(spec)), _hash(text), text, int(time.time())))
                if inserted.rowcount == 1:
                    self.store._event(None, "RND_SIMULATION_COMPUTED",
                                      "session:" + plan.session_id +
                                      " task:" + str(index) + " sha256:" + _hash(text))
            complete.add(index)
        return self.inspect(plan)

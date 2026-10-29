"""Pinned, local ngspice RC-transient adapter for unverified R&D workplans.

ONLY numeric values enter a fixed netlist; no untrusted SPICE directives,
file paths, shell, device controls, external fetches or model-generated code.
This integration does NOT provide a process sandbox or validate real hardware.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any

from .state_store import StateStore
from .schema import RESEARCH_CIRCUIT_SCHEMA, apply_schema

_EXECUTABLE_LIMIT = 64 * 1024 * 1024
_TRACE_LIMIT = 256 * 1024
_SPEC_FIELDS = {
    "resistance_ohms", "capacitance_microfarads", "supply_millivolts",
    "initial_millivolts", "duration_ms", "samples",
}


class CircuitSimulationError(ValueError):
    pass


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_circuit_spec(value: Any) -> dict:
    """Exact closed-world RC example with bounded SI-scaled integers."""
    if not isinstance(value, dict) or set(value) != _SPEC_FIELDS:
        raise CircuitSimulationError("RC circuit requires exact six parameters")
    if any(type(value[k]) is not int for k in _SPEC_FIELDS):
        raise CircuitSimulationError("RC circuit parameters must be integers")
    if not 1 <= value["resistance_ohms"] <= 1_000_000:
        raise CircuitSimulationError("Resistance outside RC template bounds")
    if not 1 <= value["capacitance_microfarads"] <= 100_000:
        raise CircuitSimulationError("Capacitance outside RC template bounds")
    if (not -100_000 <= value["supply_millivolts"] <= 100_000
            or not -100_000 <= value["initial_millivolts"] <= 100_000):
        raise CircuitSimulationError("Voltage outside RC template bounds")
    if not 1 <= value["duration_ms"] <= 10_000:
        raise CircuitSimulationError("Duration outside RC template bounds")
    if not 8 <= value["samples"] <= 128:
        raise CircuitSimulationError("Samples outside RC template bounds")
    return value


def validate_pinned_engine(path: Path) -> tuple[Path, str, int]:
    """Only operator-selected exact executable SHA is a trusted engine."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise CircuitSimulationError("Absolute operator engine config required")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError) as exc:
        raise CircuitSimulationError("Unreadable pinned ngspice config") from exc
    if not isinstance(config, dict) or set(config) != {
            "schema", "executable", "sha256", "timeout_seconds"}:
        raise CircuitSimulationError("Unknown ngspice configuration fields")
    if type(config["schema"]) is not int or config["schema"] != 1:
        raise CircuitSimulationError("Unsupported ngspice config schema")
    value = config["executable"]
    if not isinstance(value, str) or not value.strip():
        raise CircuitSimulationError("Missing ngspice executable path")
    exe = Path(value)
    if not exe.is_absolute() or not exe.is_file():
        raise CircuitSimulationError("Pinned ngspice executable unavailable")
    digest = config["sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise CircuitSimulationError("Invalid pinned executable SHA-256")
    timeout = config["timeout_seconds"]
    if type(timeout) is not int or not 1 <= timeout <= 30:
        raise CircuitSimulationError("ngspice timeout outside supported range")
    hasher = hashlib.sha256()
    total = 0
    try:
        with exe.open("rb") as inp:
            while part := inp.read(1024 * 1024):
                total += len(part)
                if total > _EXECUTABLE_LIMIT:
                    raise CircuitSimulationError("Pinned executable exceeds size limit")
                hasher.update(part)
    except OSError as exc:
        raise CircuitSimulationError("Cannot verify ngspice executable") from exc
    if hasher.hexdigest() != digest:
        raise CircuitSimulationError("Pinned executable checksum mismatch")
    return exe, digest, timeout


def build_rc_deck(spec: dict) -> str:
    """Only fixed SPICE directives, never model-supplied netlist strings."""
    p = validate_circuit_spec(spec)
    supply = p["supply_millivolts"] / 1000
    initial = p["initial_millivolts"] / 1000
    duration = p["duration_ms"] / 1000
    step = duration / p["samples"]
    return (
        "* Atlas Holding closed-world RC transient (hypothetical inputs)\n"
        f"Vsource src 0 {supply:.9g}\n"
        f"Rfixed src out {p['resistance_ohms']}\n"
        f"Cfixed out 0 {p['capacitance_microfarads']}u IC={initial:.9g}\n"
        ".control\n"
        "set wr_singlescale\n"
        "set numdgt=15\n"
        f"tran {step:.12g}s {duration:.12g}s uic\n"
        "wrdata trace.txt v(out)\n"
        "quit\n"
        ".endc\n"
        ".end\n"
    )


def _parse_trace(path: Path, duration: float) -> list[list[float]]:
    try:
        if path.stat().st_size > _TRACE_LIMIT:
            raise CircuitSimulationError("ngspice trace exceeded size limit")
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CircuitSimulationError("ngspice did not produce a readable trace") from exc
    values = []
    previous_time = -1.0
    for line in source.splitlines():
        parts = line.split()
        if len(parts) != 2:
            raise CircuitSimulationError("ngspice output must contain time and voltage")
        try:
            t, v = (float(part) for part in parts)
        except ValueError as exc:
            raise CircuitSimulationError("Non-numeric ngspice output") from exc
        if (not math.isfinite(t) or not math.isfinite(v)
                or not previous_time < t
                or not 0 <= t <= duration * 1.0001 + 1e-10
                or abs(v) > 1000):
            raise CircuitSimulationError("Invalid or nonphysical RC trace values")
        values.append([round(t, 12), round(v, 9)])
        previous_time = t
        if len(values) > 4096:
            raise CircuitSimulationError("ngspice trace has too many samples")
    if len(values) < 2:
        raise CircuitSimulationError("ngspice returned an incomplete trace")
    return values


def execute_rc(spec: dict, pinned_config: Path) -> tuple[dict, str]:
    """Run one pinned binary with a generated deck inside a temporary cwd."""
    p = validate_circuit_spec(spec)
    executable, binary_sha, timeout = validate_pinned_engine(pinned_config)
    with tempfile.TemporaryDirectory(prefix="atlas-spice-") as tmp:
        root = Path(tmp)
        (root / "rc.cir").write_text(build_rc_deck(p), encoding="utf-8")
        # -n disables loading user .spiceinit. No shell, user args or user files.
        env = os.environ.copy()
        env["HOME"] = str(root)
        env["USERPROFILE"] = str(root)
        env["TMP"] = str(root)
        env["TEMP"] = str(root)
        try:
            completed = subprocess.run(
                [str(executable), "-n", "-b", "-o", "simulator.log", "rc.cir"],
                cwd=root, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CircuitSimulationError("Pinned ngspice process unavailable or timed out") from exc
        if completed.returncode != 0:
            raise CircuitSimulationError("Pinned ngspice simulation failed")
        trace = _parse_trace(root / "trace.txt", p["duration_ms"] / 1000)
    result = {
        "status": "COMPUTED_ASSUMPTIONS_UNVALIDATED",
        "engine": "ngspice_rc_transient",
        "executable_sha256": binary_sha,
        "inputs": p,
        "trace_seconds_volts": trace,
        "final_voltage_volts": trace[-1][1],
        "note": "Simulation électrique sur hypothèses, sans mesure réelle ni preuve de matériel.",
    }
    return result, binary_sha


@dataclass(frozen=True)
class CircuitReceipt:
    session_id: str
    task_index: int
    result_sha256: str
    executable_sha256: str
    final_voltage_volts: float
    computed_epoch: int


class CircuitSimulationRunner:
    def __init__(self, store: StateStore, *, config_path: Path | None):
        if not isinstance(store, StateStore):
            raise CircuitSimulationError("Trusted state store required")
        if config_path is not None and (
                not isinstance(config_path, Path) or not config_path.is_absolute()):
            raise CircuitSimulationError("Absolute pinned ngspice config required")
        self.store = store
        self.config_path = config_path

    def inspect(self, plan) -> tuple[CircuitReceipt, ...]:
        table = self.store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='research_circuit_runs'").fetchone()
        if not table:
            return ()
        records = self.store.db.execute(
            "SELECT * FROM research_circuit_runs WHERE session_id=? "
            "ORDER BY task_index", (plan.session_id,),
        ).fetchall()
        result = []
        for row in records:
            index = row["task_index"]
            if not isinstance(index, int) or not 0 <= index < len(plan.tasks):
                raise CircuitSimulationError("Stored circuit task index invalid")
            task = plan.tasks[index]
            circuit = task.get("circuit")
            if (task["kind"] != "simulation" or circuit is None
                    or row["plan_sha256"] != plan.plan_sha256
                    or row["circuit_sha256"] != _sha(_canonical(circuit))):
                raise CircuitSimulationError("Stored circuit provenance mismatch")
            try:
                entry = json.loads(row["result_json"])
                if (_sha(row["result_json"]) != row["result_sha256"]
                        or entry["status"] != "COMPUTED_ASSUMPTIONS_UNVALIDATED"
                        or entry["engine"] != "ngspice_rc_transient"
                        or entry["inputs"] != circuit
                        or entry["executable_sha256"] != row["executable_sha256"]
                        or not isinstance(entry["trace_seconds_volts"], list)
                        or len(entry["trace_seconds_volts"]) < 2):
                    raise CircuitSimulationError("Stored circuit result integrity mismatch")
                last = entry["trace_seconds_volts"][-1]
                if (not isinstance(last, list) or len(last) != 2
                        or not all(isinstance(v, (float, int)) and math.isfinite(v)
                                   for v in last)
                        or abs(last[1] - entry["final_voltage_volts"]) > 1e-9):
                    raise CircuitSimulationError("Stored circuit trace invalid")
            except (KeyError, TypeError, ValueError) as exc:
                raise CircuitSimulationError("Stored circuit record damaged") from exc
            result.append(CircuitReceipt(
                plan.session_id, index, row["result_sha256"],
                row["executable_sha256"], float(last[1]), row["computed_epoch"]))
        return tuple(result)

    def deferred_reasons(self, plan) -> dict[int, str]:
        reasons = {}
        for index, task in enumerate(plan.tasks):
            if "circuit" not in task:
                continue
            prefix = (f"session:{plan.session_id} plan:{plan.plan_sha256} "
                      f"task:{index} reason:")
            row = self.store.db.execute(
                "SELECT detail FROM events WHERE event='RND_CIRCUIT_DEFERRED' "
                "AND detail LIKE ? ORDER BY seq DESC LIMIT 1", (prefix + "%",)
            ).fetchone()
            if row:
                reasons[index] = row["detail"][len(prefix):]
        return reasons

    def run_available(self, plan, *, numeric_done: set[int],
                      attempted: set[int] | None = None) -> tuple[CircuitReceipt, ...]:
        if self.store.background_suspended():
            raise CircuitSimulationError("Simulations paused or stopped")
        if plan.state != "STAGED":
            raise CircuitSimulationError("Only staged workplans may run circuit simulation")
        finished = {r.task_index for r in self.inspect(plan)}
        attempted = attempted if attempted is not None else set()
        if self.config_path is None:
            return self.inspect(plan)
        for index, task in enumerate(plan.tasks):
            if (index in finished or index in attempted or task["kind"] != "simulation"
                    or "circuit" not in task
                    or any(dep not in finished | numeric_done
                           for dep in task["depends_on"])):
                continue
            circuit = validate_circuit_spec(task["circuit"])
            attempted.add(index)
            try:
                result, exe_sha = execute_rc(circuit, self.config_path)
            except CircuitSimulationError as exc:
                # A failed native task is deferred, never completed. Continue
                # independent tasks; corruption of persisted results still
                # raises from inspect(), and Pause/STOP still aborts the run.
                detail = (f"session:{plan.session_id} plan:{plan.plan_sha256} "
                          f"task:{index} reason:{str(exc)[:240]}")
                with self.store._transaction():
                    if self.store.background_suspended():
                        raise CircuitSimulationError("Simulations paused or stopped") from exc
                    if not self.store.db.execute(
                            "SELECT 1 FROM events WHERE event='RND_CIRCUIT_DEFERRED' "
                            "AND detail=? LIMIT 1", (detail,)).fetchone():
                        self.store._event(None, "RND_CIRCUIT_DEFERRED", detail)
                continue
            encoded = _canonical(result)
            with self.store._transaction():
                if self.store.background_suspended():
                    raise CircuitSimulationError("Simulation paused during computation")
                active = self.store.db.execute(
                    "SELECT plan_sha256,state FROM project_workplans WHERE session_id=?",
                    (plan.session_id,),
                ).fetchone()
                if (active is None or active["plan_sha256"] != plan.plan_sha256
                        or active["state"] != "STAGED"):
                    raise CircuitSimulationError("Workplan changed during ngspice run")
                apply_schema(self.store.db, RESEARCH_CIRCUIT_SCHEMA)
                inserted = self.store.db.execute(
                    "INSERT OR IGNORE INTO research_circuit_runs "
                    "(session_id,task_index,plan_sha256,circuit_sha256,"
                    "executable_sha256,result_sha256,result_json,computed_epoch)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (plan.session_id, index, plan.plan_sha256,
                     _sha(_canonical(circuit)), exe_sha, _sha(encoded),
                     encoded, int(time.time())),
                )
                if inserted.rowcount == 1:
                    self.store._event(
                        None, "RND_CIRCUIT_COMPUTED",
                        f"session:{plan.session_id} task:{index} sha256:{_sha(encoded)}",
                    )
            finished.add(index)
        return self.inspect(plan)

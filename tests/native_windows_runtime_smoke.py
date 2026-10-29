"""Native Windows nonrelease smoke against real frozen GUI + engine binaries.

The host creates actual durable SQLite + authenticated loopback IPC; the
compiled GUI must use its private inherited stdin pipe to retrieve the
projection. No tokens, engine commands, or secrets appear in argv/logs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

# This standalone CI script is invoked as tests/<file>.py; unlike the unit
# discovery runner, direct execution doesn't include the checkout in sys.path.
# Resolve ONLY the tested checkout, never import from a user-controlled path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from holding.dashboard_ipc import DashboardIpcClient, DashboardIpcError
from holding.windows_tree_ram import MemorySampler


def run(viewer: Path, engine: Path, *, ram_receipt: Path | None = None) -> None:
    if os.name != "nt":
        raise RuntimeError("Native runtime proof requires actual Windows")
    for exe in (viewer, engine):
        if not exe.is_absolute() or not exe.is_file() or exe.is_symlink():
            raise ValueError("Real absolute Windows executable is required")
    read_key = os.urandom(32)
    # The frozen ENGINE hosts the actual SQLite-backed IPC server. No Python
    # interpreter, installed package or parent-owned server is used for this
    # native wire test. The secret crosses only an anonymous stdin pipe.
    worker = subprocess.Popen(
        [str(engine), "--native-ipc-smoke"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, shell=False, close_fds=True,
    )
    sampler = MemorySampler(interval=0.05)
    sampler.add_root(worker.pid)
    sampler.start()
    ram_observed = None
    try:
        if worker.stdin is None or worker.stdout is None:
            raise RuntimeError("Frozen engine diagnostic pipes unavailable")
        worker.stdin.write(read_key)
        worker.stdin.close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(worker.stdout.readline).result(timeout=25)
        try:
            response = json.loads(ready)
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError("Frozen engine did not provide native IPC address")
        if (type(response) is not dict
                or set(response) != {"schema", "port", "engine_id"}
                or type(response["schema"]) is not int
                or response["schema"] != 1
                or response["engine_id"] != "frozen-engine-smoke-v1"
                or type(response["port"]) is not int
                or not 1 <= response["port"] <= 65535):
            raise RuntimeError("Frozen engine IPC handshake was invalid")
        argv = [str(viewer), "--probe", "--readonly",
                "--port", str(response["port"]),
                "--engine-id", response["engine_id"]]
        # Reject a bad capability with the protocol client before launching
        # the cold frozen Qt process. Starting two one-file Qt executables in
        # sequence can exhaust the engine diagnostic's bounded 18-second
        # lifetime on a fresh runner and turn a valid second request into a
        # timing failure. The positive path below still crosses the real
        # frozen GUI and engine boundary.
        denied = DashboardIpcClient(
            port=response["port"], secret=os.urandom(32),
            engine_id=response["engine_id"],
        )
        try:
            denied.fetch()
        except DashboardIpcError:
            pass
        else:
            raise RuntimeError("Frozen engine accepted untrusted GUI credentials")
        # Observe the actual authenticated, frozen Qt GUI and engine
        # simultaneously, including PyInstaller-spawned descendants.
        valid = subprocess.Popen(
            argv, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False, close_fds=True,
        )
        sampler.add_root(valid.pid)
        try:
            valid.communicate(input=read_key, timeout=45)
        except subprocess.TimeoutExpired:
            valid.kill()
            valid.wait(timeout=5)
            raise
        if valid.returncode != 0:
            raise RuntimeError("Frozen GUI could not authenticate to frozen engine")
        try:
            proof = worker.wait(timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Frozen engine did not record signed native request") from exc
        if proof != 0:
            raise RuntimeError("Frozen engine did not certify its local IPC probe")
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        if worker.stdout is not None:
            worker.stdout.close()
        # No sample, unseen root or an inaccessible Holding descendant
        # must fail; a single-UI-PID trace is not sufficient evidence.
        ram_observed = sampler.finish()
    # Standalone durable engine SQLite offline check is independent of the
    # authenticated IPC check and catches missing frozen runtime modules.
    engine_ok = subprocess.run(
        [str(engine), "--self-test"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=50, check=False, shell=False,
    )
    if engine_ok.returncode != 0:
        raise RuntimeError("Frozen engine failed actual offline SQLite self-test")
    if ram_receipt is not None:
        if (not ram_receipt.is_absolute() or ram_receipt.exists()
                or ram_receipt.is_symlink()
                or not ram_receipt.parent.is_dir()):
            raise ValueError("New absolute RAM evidence path required")
        encoded = json.dumps(ram_observed, sort_keys=True, separators=(",", ":")) + "\n"
        with ram_receipt.open("x", encoding="utf-8") as output:
            output.write(encoded)
    print("NONRELEASE_FROZEN_ENGINE_TO_FROZEN_GUI_IPC_PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--ram-receipt", type=Path)
    args = parser.parse_args()
    run(args.viewer, args.engine, ram_receipt=args.ram_receipt)


if __name__ == "__main__":
    main()

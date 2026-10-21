"""Native Windows nonrelease smoke against real frozen GUI + engine binaries.

The host creates actual durable SQLite + authenticated loopback IPC; the
compiled GUI must use its private inherited stdin pipe to retrieve the
projection. No tokens, engine commands, or secrets appear in argv/logs.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

# This standalone CI script is invoked as tests/<file>.py; unlike the unit
# discovery runner, direct execution doesn't include the checkout in sys.path.
# Resolve ONLY the tested checkout, never import from a user-controlled path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from holding.dashboard_ipc import DashboardIpcServer
from holding.state_store import StateStore


def run(viewer: Path, engine: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Native runtime proof requires actual Windows")
    for exe in (viewer, engine):
        if not exe.is_absolute() or not exe.is_file() or exe.is_symlink():
            raise ValueError("Real absolute Windows executable is required")
    read_key = os.urandom(32)
    with tempfile.TemporaryDirectory(prefix="atlas-runtime-ipc-") as tmp:
        database = Path(tmp) / "holding.sqlite"
        with StateStore(database):
            pass
        with DashboardIpcServer.for_database(
                database, engine_id="native-runtime-proof", secret=read_key) as server:
            argv = [str(viewer), "--probe", "--readonly",
                    "--port", str(server.port),
                    "--engine-id", "native-runtime-proof"]
            common = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "timeout": 50,
                "shell": False,
                "check": False,
            }
            result = subprocess.run(argv, input=read_key, **{
                key: value for key, value in common.items()
                if key != "stdin"
            })
            if result.returncode != 0:
                raise RuntimeError(
                    "Frozen GUI rejected real signed local IPC projection")
            denied = subprocess.run(argv, input=os.urandom(32), **{
                key: value for key, value in common.items()
                if key != "stdin"
            })
            if denied.returncode != 2:
                raise RuntimeError(
                    "Frozen GUI failed to reject unauthenticated local IPC")
    engine_ok = subprocess.run(
        [str(engine), "--self-test"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=50, check=False, shell=False,
    )
    if engine_ok.returncode != 0:
        raise RuntimeError("Frozen engine failed actual offline SQLite self-test")
    print("NONRELEASE_AUTHENTICATED_GUI_ENGINE_RUNTIME_PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer", required=True, type=Path)
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()
    run(args.viewer, args.engine)


if __name__ == "__main__":
    main()

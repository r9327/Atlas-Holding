"""Installed Control Center viewer entrypoint: capabilities only via private stdin.

The trusted Windows launcher must create a new protected pipe, supply fresh
per-service session credentials, close the writer, and enforce OS ACLs/identity.
No token is accepted from argv, environment, roadmap, URL, or configuration.

This entrypoint does NOT provision Windows ACLs and is not a release installer.
"""
from __future__ import annotations

import argparse
import sys
from typing import BinaryIO

from .dashboard_ipc import (
    DashboardControlClient, DashboardIpcClient, DashboardIpcError,
)


class ViewerEnrollmentError(ValueError):
    pass


def read_capabilities(stream: BinaryIO, *, readonly: bool) -> tuple[bytes, bytes | None]:
    """Consume a finite exact frame from the trusted one-shot OS pipe."""
    if type(readonly) is not bool or stream is None:
        raise ViewerEnrollmentError("Invalid viewer enrollment context")
    try:
        if stream.isatty():
            raise ViewerEnrollmentError("Interactive stdin cannot supply credentials")
        expected = 32 if readonly else 64
        # One extra byte rejects tails (including an unexpected write key).
        raw = stream.read(expected + 1)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ViewerEnrollmentError("Private enrollment pipe unavailable") from exc
    if type(raw) is not bytes or len(raw) != expected:
        raise ViewerEnrollmentError("Invalid trusted enrollment frame")
    read_key = raw[:32]
    write_key = raw[32:] or None
    if write_key is not None and read_key == write_key:
        raise ViewerEnrollmentError("Read and write capabilities must be independent")
    return read_key, write_key


def _run_gui(
    reader: DashboardIpcClient,
    controller: DashboardControlClient | None,
) -> int:
    from PySide6.QtWidgets import QApplication
    from .control_center_live import LiveControlCenterWindow

    app = QApplication.instance() or QApplication([])
    window = LiveControlCenterWindow(reader, control=controller)
    window.show()
    return int(app.exec())


def main(argv: list[str] | None = None, *, input_stream: BinaryIO | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-control-center")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--engine-id", required=True)
    parser.add_argument("--readonly", action="store_true")
    args = parser.parse_args(argv)
    try:
        read_key, write_key = read_capabilities(
            sys.stdin.buffer if input_stream is None else input_stream,
            readonly=args.readonly,
        )
        reader = DashboardIpcClient(
            port=args.port, secret=read_key, engine_id=args.engine_id,
        )
        controller = (
            DashboardControlClient(port=args.port, secret=write_key)
            if write_key is not None else None
        )
    except (ViewerEnrollmentError, DashboardIpcError):
        print("Control Center: trusted enrollment unavailable", file=sys.stderr)
        return 2
    return _run_gui(reader, controller)


if __name__ == "__main__":
    raise SystemExit(main())

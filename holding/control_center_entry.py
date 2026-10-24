"""Installed Control Center viewer entrypoint: capabilities only via private stdin.

The trusted Windows launcher must create a new protected pipe, supply fresh
per-service session credentials, close the writer, and enforce OS ACLs/identity.
No token is accepted from argv, environment, roadmap, URL, or configuration.

This entrypoint does NOT provision Windows ACLs and is not a release installer.
"""
from __future__ import annotations

import argparse
from pathlib import Path
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


def _windows_private_pipe(readonly: bool) -> BinaryIO:
    """Read an inherited anonymous Windows pipe without a console.

    PyInstaller --windowed may set sys.stdin = None. GetStdHandle still
    addresses the actual private pipe inherited from our trusted parent.
    Reject terminals, regular files and absent/invalid handles.
    """
    import io
    import os
    if os.name != "nt":
        raise ViewerEnrollmentError("Private enrollment stdin unavailable")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel.ReadFile.restype = wintypes.BOOL
    handle = kernel.GetStdHandle(0xFFFFFFF6)  # STD_INPUT_HANDLE (-10)
    if (not handle or handle == ctypes.c_void_p(-1).value
            or kernel.GetFileType(handle) != 3):  # FILE_TYPE_PIPE
        raise ViewerEnrollmentError("Trusted Windows pipe not available")
    expected = 32 if readonly else 64
    data = bytearray()
    while len(data) <= expected:
        size = expected + 1 - len(data)
        buffer = ctypes.create_string_buffer(size)
        received = wintypes.DWORD()
        if not kernel.ReadFile(
                handle, buffer, size, ctypes.byref(received), None):
            # Broken pipe after parent EOF is normal. All other conditions
            # fail closed; no console or disk fallback is attempted.
            if ctypes.get_last_error() == 109:
                break
            raise ViewerEnrollmentError("Private Windows pipe read failed")
        if not received.value:
            break
        data.extend(buffer.raw[:received.value])
    return io.BytesIO(bytes(data))


def _enrollment_input(*, readonly: bool, injected: BinaryIO | None) -> BinaryIO:
    if injected is not None:
        return injected
    source = getattr(sys.stdin, "buffer", None)
    if source is not None:
        return source
    return _windows_private_pipe(readonly)


def _run_gui(
    reader: DashboardIpcClient,
    controller: DashboardControlClient | None,
    *, update_root: Path | None = None,
) -> int:
    from PySide6.QtWidgets import QApplication
    from .control_center_live import LiveControlCenterWindow

    app = QApplication.instance() or QApplication([])
    window = LiveControlCenterWindow(
        reader, control=controller, update_root=update_root,
    )
    window.show()
    return int(app.exec())


def _self_test() -> int:
    """Offline smoke: instantiate the actual Qt window without any credentials."""
    from PySide6.QtWidgets import QApplication
    from .control_center_ui import ControlCenterWindow

    app = QApplication.instance() or QApplication([])
    window = ControlCenterWindow(trusted_engine_id="bundled-self-test")
    if window.objectName() != "atlasControlCenter":
        return 2
    window.close()
    return 0


def main(argv: list[str] | None = None, *, input_stream: BinaryIO | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-control-center")
    parser.add_argument("--port", type=int)
    parser.add_argument("--engine-id")
    parser.add_argument("--readonly", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument(
        "--update-root", type=Path,
        help="Display-only installation root, never authorizes a release",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        if (args.port is not None or args.engine_id is not None
                or args.readonly or args.probe or args.update_root is not None):
            return 2
        return _self_test()
    if args.port is None or args.engine_id is None:
        parser.error("--port and --engine-id are required outside self-test")
    if args.probe and args.update_root is not None:
        parser.error("--update-root is display-only and invalid with --probe")
    try:
        read_key, write_key = read_capabilities(
            _enrollment_input(readonly=args.readonly, injected=input_stream),
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
        if sys.stderr is not None:
            print("Control Center: trusted enrollment unavailable", file=sys.stderr)
        return 2
    if args.probe:
        # Test the actual authenticated frozen protocol, not an unauthenticated
        # port-open check. A probe cannot change engine state.
        try:
            reader.fetch()
        except DashboardIpcError:
            return 2
        return 0
    return _run_gui(reader, controller, update_root=args.update_root)


if __name__ == "__main__":
    raise SystemExit(main())

"""Authenticated, bounded, read-only loopback transport for Control Center.

No SQLite connection or privileged command is available to the GUI. The host
owns the database and supplies only a sanitized, immutable projection. This
transport authenticates messages but does NOT prove Windows process isolation
or secure provisioning of the secret; installer ACL enforcement is a release gate.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import socket
import socketserver
import struct
import threading
import time
from typing import Callable

from .control_center_state import (
    MAX_SNAPSHOT_BYTES, ControlCenterStateError, DashboardState,
    consume_dashboard_snapshot,
)

_MAGIC = b"AH01"
_REQUEST_BYTES = 4 + 32 + 32
_MAX_CLIENTS = 4
_MAX_TIME_SECONDS = 3


class DashboardIpcError(RuntimeError):
    """Transport unavailable, unauthenticated, or malformed."""


def _secret(value: bytes) -> bytes:
    if type(value) is not bytes or len(value) < 32 or len(value) > 128:
        raise DashboardIpcError("Trusted dashboard key must contain 32-128 bytes")
    return value


def _read_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        piece = sock.recv(length - len(data))
        if not piece:
            raise DashboardIpcError("Incomplete dashboard frame")
        data.extend(piece)
    return bytes(data)


class _DashboardTCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(self, key: bytes, supplier: Callable[[], bytes]):
        self.key = key
        self.supplier = supplier
        self.slots = threading.BoundedSemaphore(_MAX_CLIENTS)
        self.nonces: OrderedDict[bytes, None] = OrderedDict()
        self.nonce_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _DashboardHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class _DashboardHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        sock = self.request
        sock.settimeout(_MAX_TIME_SECONDS)
        try:
            request = _read_exact(sock, _REQUEST_BYTES)
            magic, nonce, signature = request[:4], request[4:36], request[36:]
            expected = hmac.new(self.server.key, b"request" + nonce, hashlib.sha256).digest()
            if magic != _MAGIC or not hmac.compare_digest(signature, expected):
                return
            # Captured local frames must not become reusable read capabilities.
            with self.server.nonce_lock:
                if nonce in self.server.nonces:
                    return
                self.server.nonces[nonce] = None
                if len(self.server.nonces) > 512:
                    self.server.nonces.popitem(last=False)
            raw = self.server.supplier()
            if type(raw) is not bytes or not 1 <= len(raw) <= MAX_SNAPSHOT_BYTES:
                return
            length = struct.pack("!I", len(raw))
            mac = hmac.new(
                self.server.key, b"response" + nonce + length + raw,
                hashlib.sha256,
            ).digest()
            sock.sendall(_MAGIC + length + raw + mac)
        except (DashboardIpcError, OSError, TimeoutError):
            # Never leak filesystem locations, DB errors or credentials.
            return
        except Exception:
            # Projection failures are not grounds for disclosing internals.
            return


class DashboardIpcServer:
    """Trusted host's independent local read-only server, no control commands."""

    def __init__(self, secret: bytes, supplier: Callable[[], bytes]):
        if not callable(supplier):
            raise DashboardIpcError("Trusted projection supplier required")
        self._server = _DashboardTCP(_secret(secret), supplier)
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @classmethod
    def for_database(cls, database: Path, *, engine_id: str, secret: bytes):
        if (not isinstance(database, Path) or not database.is_absolute()
                or not database.is_file() or database.is_symlink()):
            raise DashboardIpcError("Existing trusted database required")
        if not isinstance(engine_id, str) or not engine_id:
            raise DashboardIpcError("Trusted engine identity required")

        def project() -> bytes:
            from .control import ControlPlane
            from .control_center_projection import project_dashboard_snapshot
            with ControlPlane(database) as control:
                return project_dashboard_snapshot(
                    control, engine_id=engine_id, now_epoch=int(time.time()),
                )
        return cls(secret, project)

    def start(self) -> None:
        if self._thread is not None:
            raise DashboardIpcError("Dashboard server already started")
        thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="atlas-dashboard-readonly",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._server.server_close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_ignored):
        self.close()


class DashboardIpcClient:
    """GUI-side read-only client. Never sends Python objects or commands."""

    def __init__(self, *, port: int, secret: bytes, engine_id: str):
        if type(port) is not int or not 1 <= port <= 65535:
            raise DashboardIpcError("Invalid trusted dashboard port")
        if not isinstance(engine_id, str) or not engine_id:
            raise DashboardIpcError("Invalid trusted engine identity")
        self.port = port
        self._secret = _secret(secret)
        self._engine_id = engine_id
        self._last_sequence: int | None = None

    @property
    def engine_id(self) -> str:
        return self._engine_id

    def fetch(self, *, now_epoch: int | None = None) -> DashboardState:
        nonce = secrets.token_bytes(32)
        mac = hmac.new(self._secret, b"request" + nonce, hashlib.sha256).digest()
        try:
            with socket.create_connection(("127.0.0.1", self.port),
                                          timeout=_MAX_TIME_SECONDS) as sock:
                sock.settimeout(_MAX_TIME_SECONDS)
                sock.sendall(_MAGIC + nonce + mac)
                header = _read_exact(sock, 8)
                if header[:4] != _MAGIC:
                    raise DashboardIpcError("Unauthenticated dashboard reply")
                size = struct.unpack("!I", header[4:])[0]
                if not 1 <= size <= MAX_SNAPSHOT_BYTES:
                    raise DashboardIpcError("Oversized dashboard frame")
                payload = _read_exact(sock, size)
                received = _read_exact(sock, 32)
        except (OSError, TimeoutError) as exc:
            raise DashboardIpcError("Dashboard service unreachable") from exc
        expected = hmac.new(
            self._secret, b"response" + nonce + header[4:] + payload,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, received):
            raise DashboardIpcError("Unauthenticated dashboard snapshot")
        try:
            # Decode only authenticated bytes. The projection consumer
            # rejects duplicate keys, invalid states, stale age and forged QA.
            metadata = json.loads(payload)
            if type(metadata) is not dict:
                raise ValueError("Invalid dashboard object")
            sequence = metadata.get("sequence")
            state = consume_dashboard_snapshot(
                payload, trusted_engine_id=self._engine_id,
                expected_sequence=sequence,
                now_epoch=int(time.time()) if now_epoch is None else now_epoch,
            )
        except (ValueError, TypeError, ControlCenterStateError, RecursionError) as exc:
            raise DashboardIpcError("Invalid authenticated dashboard snapshot") from exc
        if self._last_sequence is not None and state.sequence < self._last_sequence:
            raise DashboardIpcError("Replayed older dashboard sequence")
        self._last_sequence = state.sequence
        return state

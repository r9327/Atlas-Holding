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
_CONTROL_MAGIC = b"AH02"
_REQUEST_BYTES = 4 + 32 + 32
_CONTROL_BYTES = 1 + 8 + 32 + 32
_CONTROL_ACTIONS = {"pause": b"P", "resume": b"R", "stop": b"S"}
_CONTROL_TTL_SECONDS = 30
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

    def __init__(self, key: bytes, supplier: Callable[[], bytes], *,
                 control_key: bytes | None = None,
                 control_handler: Callable[[bytes], None] | None = None):
        self.key = key
        self.supplier = supplier
        self.control_key = control_key
        self.control_handler = control_handler
        self.slots = threading.BoundedSemaphore(_MAX_CLIENTS)
        self.nonces: OrderedDict[bytes, None] = OrderedDict()
        self.control_nonces: OrderedDict[bytes, int] = OrderedDict()
        self.nonce_lock = threading.Lock()
        self.valid_read_replied = threading.Event()
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
            magic = _read_exact(sock, 4)
            if magic == _CONTROL_MAGIC:
                self._control(sock)
                return
            if magic != _MAGIC:
                return
            request = _read_exact(sock, _REQUEST_BYTES - 4)
            nonce, signature = request[:32], request[32:]
            expected = hmac.new(self.server.key, b"request" + nonce, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
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
            # Mark only a completed authenticated response, never just a
            # registered nonce or a failed supplier; used by native E2E proof.
            self.server.valid_read_replied.set()
        except (DashboardIpcError, OSError, TimeoutError):
            # Never leak filesystem locations, DB errors or credentials.
            return
        except Exception:
            # Projection failures are not grounds for disclosing internals.
            return

    def _control(self, sock: socket.socket) -> None:
        """Separate write grant; never accept a command signed by a read key."""
        key = self.server.control_key
        handler = self.server.control_handler
        if key is None or handler is None:
            return
        message = _read_exact(sock, _CONTROL_BYTES)
        op, timestamp, nonce, signature = (
            message[:1], message[1:9], message[9:41], message[41:],
        )
        if op not in _CONTROL_ACTIONS.values():
            return
        epoch = struct.unpack("!Q", timestamp)[0]
        now = int(time.time())
        if abs(now - epoch) > _CONTROL_TTL_SECONDS:
            return
        expected = hmac.new(
            key, b"control-request" + op + timestamp + nonce,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            return
        with self.server.nonce_lock:
            # Never evict a still-valid nonce: at capacity reject new commands
            # rather than admitting a replay of an older control request.
            for saved, created in tuple(self.server.control_nonces.items()):
                if now - created > 2 * _CONTROL_TTL_SECONDS:
                    del self.server.control_nonces[saved]
            if nonce in self.server.control_nonces or len(self.server.control_nonces) >= 512:
                return
            self.server.control_nonces[nonce] = now
        try:
            handler(op)
        except Exception:
            result = b"N"
        else:
            result = b"A"
        confirmation = hmac.new(
            key, b"control-response" + op + timestamp + nonce + result,
            hashlib.sha256,
        ).digest()
        sock.sendall(_CONTROL_MAGIC + result + confirmation)


class DashboardIpcServer:
    """Trusted host's independent local read-only server, no control commands."""

    def __init__(self, secret: bytes, supplier: Callable[[], bytes], *,
                 control_secret: bytes | None = None,
                 control_handler: Callable[[bytes], None] | None = None):
        if not callable(supplier):
            raise DashboardIpcError("Trusted projection supplier required")
        if (control_secret is None) != (control_handler is None):
            raise DashboardIpcError("Control key and trusted handler required together")
        if control_secret is not None and (not callable(control_handler)
                                            or _secret(control_secret) == secret):
            raise DashboardIpcError("Separate trusted write capability required")
        self._server = _DashboardTCP(
            _secret(secret), supplier, control_key=control_secret,
            control_handler=control_handler,
        )
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @classmethod
    def for_database(cls, database: Path, *, engine_id: str, secret: bytes,
                     control_secret: bytes | None = None):
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
        def apply_control(operation: bytes) -> None:
            from .control import ControlPlane
            with ControlPlane(database) as control:
                if operation == b"P":
                    control.pause()
                elif operation == b"R":
                    # STOP is durable: RESUME must never clear a STOP request.
                    if control.snapshot().stop_requested:
                        raise DashboardIpcError("STOP must be reset by trusted installer")
                    control.resume()
                elif operation == b"S":
                    control.request_stop()
                else:
                    raise DashboardIpcError("Unknown control operation")

        return cls(
            secret, project,
            control_secret=control_secret,
            control_handler=apply_control if control_secret is not None else None,
        )

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


class DashboardControlClient:
    """Separate write-authority client; deliberately cannot fetch snapshots.

    Installers must provision a fresh short-lived secret over a private,
    ACL-protected OS boundary. The key is never stored by this module.
    """

    def __init__(self, *, port: int, secret: bytes):
        if type(port) is not int or not 1 <= port <= 65535:
            raise DashboardIpcError("Invalid trusted control endpoint")
        self.port = port
        self._secret = _secret(secret)

    def send(self, action: str) -> None:
        if type(action) is not str or action not in _CONTROL_ACTIONS:
            raise DashboardIpcError("Unsupported dashboard control action")
        op = _CONTROL_ACTIONS[action]
        timestamp = struct.pack("!Q", int(time.time()))
        nonce = secrets.token_bytes(32)
        mac = hmac.new(
            self._secret, b"control-request" + op + timestamp + nonce,
            hashlib.sha256,
        ).digest()
        frame = _CONTROL_MAGIC + op + timestamp + nonce + mac
        try:
            with socket.create_connection(
                ("127.0.0.1", self.port), timeout=_MAX_TIME_SECONDS,
            ) as sock:
                sock.settimeout(_MAX_TIME_SECONDS)
                sock.sendall(frame)
                response = _read_exact(sock, 4 + 1 + 32)
        except (OSError, TimeoutError) as exc:
            raise DashboardIpcError(
                "Control acknowledgement unavailable; inspect durable state"
            ) from exc
        magic, result, signature = response[:4], response[4:5], response[5:]
        expected = hmac.new(
            self._secret,
            b"control-response" + op + timestamp + nonce + result,
            hashlib.sha256,
        ).digest()
        if (magic != _CONTROL_MAGIC or result not in {b"A", b"N"}
                or not hmac.compare_digest(signature, expected)):
            raise DashboardIpcError(
                "Control acknowledgement unauthenticated; inspect durable state"
            )
        if result != b"A":
            raise DashboardIpcError("Control refused; inspect durable state")

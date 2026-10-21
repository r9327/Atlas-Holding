"""End-to-end pinned subprocess launch and private capability delivery."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from holding.control import ControlPlane
from holding.dashboard_session import (
    DashboardSessionError, ViewerProgram,
)
from holding.service import HoldingService, ServiceSettings
from holding.state_store import StateStore


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IdleWorker:
    def ready(self):
        return True

    def __call__(self, _mission):
        raise AssertionError("GUI must not trigger coding")


class DashboardSessionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.db = self.root / "engine.sqlite"
        with StateStore(self.db):
            pass
        self.binary = Path(sys.executable).resolve()
        self.script = self.root / "verified-viewer.py"
        self.marker = self.root / "viewer-proof"
        # This independent child authenticates a live read and a real Pause
        # using its private stdin keys, never a token embedded in argv or env.
        self.script.write_text(
            """import argparse,hashlib,hmac,json,socket,struct,sys,time
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--port',type=int,required=True)
p.add_argument('--engine-id',required=True)
p.add_argument('--readonly',action='store_true')
a=p.parse_args()
keys=sys.stdin.buffer.read(65)
assert len(keys)==(32 if a.readonly else 64), len(keys)
assert all(v not in ' '.join(sys.argv) for v in (keys[:32].hex(),keys[-32:].hex()))
def recv(sock,count):
    data=b''
    while len(data)<count:
        b=sock.recv(count-len(data))
        assert b
        data+=b
    return data
with socket.create_connection(('127.0.0.1',a.port),timeout=3) as s:
    s.settimeout(3)
    n=b'x'*32
    s.sendall(b'AH01'+n+hmac.new(keys[:32],b'request'+n,hashlib.sha256).digest())
    head=recv(s,8)
    assert head[:4]==b'AH01'
    length=struct.unpack('!I',head[4:])[0]
    raw=recv(s,length)
    mac=recv(s,32)
    assert hmac.compare_digest(mac,hmac.new(keys[:32],b'response'+n+head[4:]+raw,hashlib.sha256).digest())
    assert json.loads(raw)['engine_id']==a.engine_id
if not a.readonly:
    op=b'P'
    stamp=struct.pack('!Q',int(time.time()))
    n=b'y'*32
    signature=hmac.new(keys[32:],b'control-request'+op+stamp+n,hashlib.sha256).digest()
    with socket.create_connection(('127.0.0.1',a.port),timeout=3) as s:
        s.sendall(b'AH02'+op+stamp+n+signature)
        answer=recv(s,37)
        assert answer[:5]==b'AH02A'
Path(""" + repr(str(self.marker)) + """).write_text('OK')
time.sleep(30)
""", encoding="utf-8",
        )
        self.program = ViewerProgram(
            self.binary, digest(self.binary),
            self.script, digest(self.script),
        )

    def make_service(self):
        return HoldingService(
            ServiceSettings(self.db, idle_seconds=0.05),
            IdleWorker(), online=lambda: False,
        )

    def until(self, fn, *, seconds=5):
        expiry = time.monotonic() + seconds
        while time.monotonic() < expiry:
            if fn():
                return
            time.sleep(0.03)
        self.fail("Trusted viewer did not complete authenticated handoff")

    def test_verified_child_receives_private_keys_and_can_pause(self):
        with self.make_service() as service:
            service.start()
            pid = service.launch_dashboard(self.program)
            self.assertGreater(pid, 0)
            self.until(lambda: self.marker.is_file())
            self.assertEqual(self.marker.read_text(), "OK")
            self.assertTrue(service._dashboard_session.is_running())
            with ControlPlane(self.db) as control:
                self.assertTrue(control.snapshot().paused)
            with self.assertRaises(RuntimeError):
                service.launch_dashboard(self.program)
        self.assertFalse(service._dashboard_session)  # closed with host

    def test_read_only_child_has_no_write_capability(self):
        with self.make_service() as service:
            service.start()
            service.launch_dashboard(self.program, readonly=True)
            self.until(lambda: self.marker.is_file())
            with ControlPlane(self.db) as control:
                self.assertFalse(control.snapshot().paused)

    def test_tampered_script_is_rejected_without_opening_ipc(self):
        with self.make_service() as service:
            service.start()
            self.script.write_text("print('tampered')")
            with self.assertRaisesRegex(DashboardSessionError, "changed"):
                service.launch_dashboard(self.program)
            self.assertIsNone(service._dashboard_ipc)

    def test_fake_executable_and_broken_file_pin_fail_closed(self):
        with self.assertRaises(DashboardSessionError):
            ViewerProgram(self.binary, "0" * 64).verify()
        with self.assertRaises(DashboardSessionError):
            ViewerProgram(self.binary, digest(self.binary), self.script).verify()
        with self.assertRaises(DashboardSessionError):
            ViewerProgram(Path("relative.exe"), "f" * 64).verify()
        with self.assertRaises(DashboardSessionError):
            ViewerProgram(self.binary, digest(self.binary),
                          self.binary, digest(self.binary)).verify()

    def test_symlinked_script_is_refused(self):
        link = self.root / "script-link.py"
        try:
            link.symlink_to(self.script)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks disabled by test host policy")
        with self.assertRaises(DashboardSessionError):
            ViewerProgram(self.binary, digest(self.binary),
                          link, digest(self.script)).verify()

    def test_daemon_start_required_and_failed_popen_releases_listener(self):
        from unittest.mock import patch
        with self.make_service() as service:
            with self.assertRaises(DashboardSessionError):
                service.launch_dashboard(self.program)
            service.start()
            with patch("holding.dashboard_session.subprocess.Popen",
                       side_effect=OSError("unavailable")):
                with self.assertRaises(OSError):
                    service.launch_dashboard(self.program)
            self.assertIsNone(service._dashboard_ipc)
            self.assertIsNone(service._dashboard_session)


if __name__ == "__main__":
    unittest.main()

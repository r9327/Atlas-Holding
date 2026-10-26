"""First actual cross-PR contract: pinned Windows setup JSON -> engine schema v3.

Uses exact source modules in the candidate tree, without model downloads,
privileged ACL modifications, shell execution, or claiming operational health.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from holding.daemon_entry import Installation, BootstrapError
from holding.windows_engine_enrollment import EngineEnrollmentRequest, _document


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FrozenEngineEnrollmentContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / "selected-application-disk" / "Atlas Holding"
        self.runtime = self.root / "runtime" / "pinned-build"
        self.agent_dir = self.root / "agents"
        self.data = self.root / "data"
        self.config_dir = self.root / "config"
        self.workspaces = self.root / "workspaces"
        self.health = self.root / "health"
        self.canonical = self.base / "existing-target-clone"
        for directory in (self.runtime, self.agent_dir, self.data,
                          self.config_dir, self.workspaces, self.health,
                          self.canonical / ".git"):
            directory.mkdir(parents=True)
        self.viewer = self.runtime / "AtlasHoldingControlCenter.exe"
        self.engine = self.runtime / "AtlasHoldingEngine.exe"
        self.agent = self.agent_dir / "AtlasHoldingAgent.exe"
        self.model = self.agent_dir / "AtlasHoldingModel.json"
        for path, data in (
            (self.viewer, b"pinned viewer"), (self.engine, b"pinned engine"),
            (self.agent, b"pinned protocol adapter"),
            (self.model, b'{"schema":1,"model":"fixture:Q4","digest":"' +
             b"a" * 64 + b'","num_ctx":4096,"timeout":600}\n'),
        ):
            path.write_bytes(data)
        self.request = EngineEnrollmentRequest(
            install_root=self.root,
            runtime_directory=self.runtime,
            viewer_sha256=_hash(self.viewer),
            engine_sha256=_hash(self.engine),
            repository="r9327/Dof-Atlas",
            canonical_root=self.canonical,
            workspace=self.workspaces,
            health_directory=self.health,
            agent_executable=self.agent,
            agent_sha256=_hash(self.agent),
            agent_arguments=(self.model,),
            agent_argument_pins={str(self.model): _hash(self.model)},
        )
        self.file = self.config_dir / "engine.json"
        self.file.write_bytes(_document(self.request))

    def test_setup_schema_loads_in_actual_integrated_engine(self):
        cfg = Installation.load(self.file)
        self.assertEqual(cfg.repository, "r9327/Dof-Atlas")
        self.assertEqual(cfg.executable, self.agent)
        self.assertEqual(cfg.executable_sha256, _hash(self.agent))
        self.assertEqual(cfg.arguments, (str(self.model),))
        self.assertEqual(cfg.argument_sha256, {
            str(self.model): _hash(self.model),
        })
        self.assertEqual(cfg.dashboard.program.executable, self.viewer)
        self.assertEqual(cfg.dashboard.program.executable_sha256,
                         _hash(self.viewer))
        self.assertIsNone(cfg.github)

    def test_pinned_model_change_refuses_engine_even_when_setup_json_unchanged(self):
        self.model.write_bytes(b'{"model":"attacker-override"}')
        with self.assertRaisesRegex(BootstrapError,
                                    "Trusted agent argument changed"):
            Installation.load(self.file)

    def test_pinned_adapter_or_viewer_change_refuses_engine(self):
        self.agent.write_bytes(b"different executable bytes")
        with self.assertRaisesRegex(BootstrapError,
                                    "Agent executable changed"):
            Installation.load(self.file)
        self.agent.write_bytes(b"pinned protocol adapter")
        self.viewer.write_bytes(b"different viewer binary")
        with self.assertRaisesRegex(BootstrapError,
                                    "dashboard binary is not verified"):
            Installation.load(self.file)

    def test_no_unverified_github_broker_is_inferred(self):
        config = Installation.load(self.file)
        self.assertIsNone(config.github)
        self.assertEqual(config.dashboard.readonly, False)
        self.assertFalse((self.workspaces / "new-mission").exists())


if __name__ == "__main__":
    unittest.main()

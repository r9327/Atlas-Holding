from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from holding.autonomy import Mission
from holding.engine import WorkerDeferred
from holding.service import HoldingService, ServiceSettings
from holding.worker_router import OperationWorkerRouter


class Adapter:
    def __init__(self, name, ready=True):
        self.name = name
        self.is_ready = ready
        self.calls = []

    def ready(self):
        return self.is_ready

    def __call__(self, mission):
        self.calls.append(mission.id)
        return self.name


class WorkerRouterTests(unittest.TestCase):
    def test_dynamic_capabilities_exclude_unhealthy_role(self):
        code = Adapter("code", ready=False)
        push = Adapter("push")
        router = OperationWorkerRouter({"code": code, "push": push})
        self.assertEqual(router.available_operations(), frozenset({"push"}))
        self.assertTrue(router.ready())
        self.assertEqual(router(Mission("p", "r9327/Dof-Atlas", operation="push")),
                         "push")
        self.assertEqual((code.calls, push.calls), ([], ["p"]))

    def test_role_health_is_rechecked_at_dispatch_boundary(self):
        code = Adapter("code")
        router = OperationWorkerRouter({"code": code})
        self.assertEqual(router.available_operations(), frozenset({"code"}))
        code.is_ready = False
        with self.assertRaisesRegex(WorkerDeferred, "NOT_READY"):
            router(Mission("c", "r9327/Dof-Atlas"))

    def test_duplicate_unknown_or_missing_role_is_rejected(self):
        router = OperationWorkerRouter({"code": Adapter("code")})
        with self.assertRaises(ValueError):
            router.add("code", Adapter("other"))
        with self.assertRaises(ValueError):
            router.add("force_push", Adapter("bad"))
        with self.assertRaisesRegex(WorkerDeferred, "UNAVAILABLE"):
            router(Mission("p", "r9327/Dof-Atlas", operation="push"))

    def test_trusted_service_hook_adds_push_before_start_only(self):
        with tempfile.TemporaryDirectory() as folder:
            git = Mock(repository="r9327/Dof-Atlas")
            git.inspect_canonical.return_value = None
            router = OperationWorkerRouter({"code": Adapter("code")})
            service = HoldingService(
                ServiceSettings(Path(folder) / "holding.sqlite"), router,
                online=lambda: False, recovery_git=git)
            try:
                service.enable_git_push(
                    expected_account_id="github:r9327",
                    expected_policy_sha="a" * 40,
                    control_factory=lambda _source: Mock())
                self.assertEqual(
                    router.available_operations(), frozenset({"code", "push"}))
                service._started = True
                with self.assertRaisesRegex(RuntimeError, "before service start"):
                    service.enable_git_push(
                        expected_account_id="github:r9327",
                        expected_policy_sha="a" * 40)
            finally:
                service._started = False
                service.close()


if __name__ == "__main__":
    unittest.main()

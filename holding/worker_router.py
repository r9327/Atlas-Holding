"""Trusted operation router with dynamic per-adapter readiness."""
from __future__ import annotations

from .autonomy import KNOWN_OPERATIONS, Mission
from .engine import WorkerDeferred


class OperationWorkerRouter:
    def __init__(self, adapters: dict[str, object]):
        if not adapters:
            raise ValueError("At least one trusted worker adapter is required")
        self._adapters: dict[str, object] = {}
        for operation, adapter in adapters.items():
            self.add(operation, adapter)

    @staticmethod
    def _valid(operation: str, adapter: object) -> bool:
        return (operation in KNOWN_OPERATIONS and callable(adapter)
                and callable(getattr(adapter, "ready", None)))

    def add(self, operation: str, adapter: object) -> None:
        if not self._valid(operation, adapter) or operation in self._adapters:
            raise ValueError("Invalid or duplicate trusted worker adapter")
        self._adapters[operation] = adapter

    def available_operations(self) -> frozenset[str]:
        available = set()
        for operation, adapter in self._adapters.items():
            try:
                if adapter.ready():
                    available.add(operation)
            except Exception:
                continue
        return frozenset(available)

    def ready(self) -> bool:
        return bool(self.available_operations())

    def __call__(self, mission: Mission) -> object:
        if not isinstance(mission, Mission):
            raise TypeError("Expected a persisted mission")
        adapter = self._adapters.get(mission.operation)
        if adapter is None:
            raise WorkerDeferred("TRUSTED_ROLE_UNAVAILABLE")
        try:
            ready = adapter.ready()
        except Exception:
            ready = False
        if not ready:
            raise WorkerDeferred("TRUSTED_ROLE_NOT_READY")
        return adapter(mission)

"""Explainable, non-mutating provisioning decisions from measured inventory.

This is not an installer and never calls a download/install routine.
The installer must consume actions under its independent authorization gate.
"""
from __future__ import annotations

from dataclasses import dataclass

from .inventory import Component, Inventory


@dataclass(frozen=True)
class ProvisionAction:
    name: str
    action: str
    reason: str


@dataclass(frozen=True)
class ProvisionPlan:
    state: str
    actions: tuple[ProvisionAction, ...]
    model_validation_required: bool
    blockers: tuple[str, ...]


def _action(name: str, component: Component) -> ProvisionAction:
    if component.status == "FOUND_VALID":
        return ProvisionAction(name, "REUSE", "Existing healthy dependency")
    if component.status == "NOT_FOUND":
        return ProvisionAction(name, "INSTALL", "Missing dependency")
    if component.status in ("FOUND_BROKEN", "FOUND_INCOMPATIBLE"):
        return ProvisionAction(name, "REPAIR", "Installed dependency is not healthy")
    return ProvisionAction(name, "INSPECT", "Unknown dependency state; do not modify")


def make_plan(snapshot: Inventory, *, online: bool) -> ProvisionPlan:
    """No fake READY without a validated coding model and toolchain benchmark."""
    blockers: list[str] = []
    if snapshot.os_name != "Windows" or snapshot.os_architecture.lower() not in ("amd64", "x86_64"):
        blockers.append("Windows x64 is required for the targeted installer")
    if snapshot.installation_disk_free_bytes < 2 * 1024**3:
        blockers.append("Less than 2 GiB free for the app runtime; model storage not included")
    items = (
        _action("git", snapshot.git),
        _action("ollama", snapshot.ollama),
        _action("opencode", snapshot.opencode),
        # The packaged interpreter is already healthy; never reinstall the
        # system Python just because a packaged runtime exists.
        _action("python_runtime", snapshot.python),
    )
    if not snapshot.models:
        if not online:
            blockers.append("No local model and network unavailable")
        # Model sizes and choices require the separate signed catalogue and
        # benchmark from PR #4; there is no guessed hardware-based READY.
        items += (ProvisionAction("coding_model", "BENCHMARK_OR_DOWNLOAD",
                                  "No verified installed model"),)
    else:
        items += (ProvisionAction("coding_model", "BENCHMARK_INSTALLED",
                                  "Installed models require real coding and tools tests"),)
    if blockers:
        state = "BLOCKED"
    elif any(item.action == "REPAIR" for item in items):
        state = "REPAIR_REQUIRED"
    elif any(item.action == "INSPECT" for item in items):
        state = "INSPECTION_REQUIRED"
    elif any(item.action in ("INSTALL", "BENCHMARK_OR_DOWNLOAD") for item in items):
        state = "PROVISIONING_REQUIRED"
    else:
        state = "BENCHMARK_REQUIRED"
    return ProvisionPlan(state, items, True, tuple(blockers))

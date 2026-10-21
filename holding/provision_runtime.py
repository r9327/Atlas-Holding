"""Execute only necessary, release-pinned dependency staging actions.

This is the bridge between the measured inventory and versioned ZIP installer.
It never modifies the PATH, starts a downloaded binary or claims an Ollama
model is ready. The packaged Windows setup remains the authority for consent,
ACLs, native service registration and post-install health checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .inventory import Inventory
from .provision_plan import make_plan
from .staged_install import (InstallError, PinnedZip, inspect_versioned_zip,
                             install_versioned_zip)
from .downloads import DownloadError
from .native_health import check_staged_binary
from .windows_package_manager import WingetPin, install_pinned_winget


@dataclass(frozen=True)
class ProvisionStep:
    name: str
    action: str
    result: str


@dataclass(frozen=True)
class ProvisionExecution:
    state: str
    steps: tuple[ProvisionStep, ...]
    blockers: tuple[str, ...]


def stage_required_dependencies(
    snapshot: Inventory, pins: tuple[PinnedZip | WingetPin, ...], *,
    online: bool, authorized: bool,
    install_root: Path, cache_root: Path,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ProvisionExecution:
    """Only stage unhealthy/missing dependencies; never auto-promote readiness.

    A preflight first checks all required release pins, so an incomplete or
    ambiguous manifest cannot cause a partial installation. Runtime tools
    already passing inventory health checks are always left alone.
    """
    if (not isinstance(snapshot, Inventory)
            or not isinstance(pins, tuple) or type(online) is not bool
            or type(authorized) is not bool
            or not isinstance(install_root, Path) or not install_root.is_absolute()
            or not isinstance(cache_root, Path) or not cache_root.is_absolute()):
        raise ValueError("Invalid trusted provision request")
    plan = make_plan(snapshot, online=online)
    if plan.blockers:
        return ProvisionExecution("BLOCKED", (), plan.blockers)
    if any(action.action == "INSPECT" for action in plan.actions):
        return ProvisionExecution("BLOCKED", (), ("Unknown dependency state",))
    manifest: dict[str, PinnedZip | WingetPin] = {}
    for spec in pins:
        if not isinstance(spec, (PinnedZip, WingetPin)):
            raise ValueError("Invalid release manifest entry")
        spec.validate()
        if spec.component in manifest:
            return ProvisionExecution("BLOCKED", (), ("Duplicate component pin",))
        manifest[spec.component] = spec

    needed = [action for action in plan.actions
              if action.action in {"INSTALL", "REPAIR"}]
    missing = [action.name for action in needed if action.name not in manifest]
    if missing:
        return ProvisionExecution(
            "BLOCKED", (), tuple("No release-pinned payload for " + name
                                  for name in missing),
        )

    steps: list[ProvisionStep] = [
        ProvisionStep(action.name, action.action, "REUSED_HEALTHY")
        for action in plan.actions if action.action == "REUSE"
    ]
    if needed and not authorized:
        steps.extend(ProvisionStep(action.name, action.action, "AWAITING_APPROVAL")
                     for action in needed)
        return ProvisionExecution("AUTH_REQUIRED", tuple(steps), ())

    for action in needed:
        if cancelled is not None and cancelled():
            return ProvisionExecution(
                "PARTIAL_OR_BLOCKED", tuple(steps), ("Installation cancelled",),
            )
        spec = manifest[action.name]
        if isinstance(spec, WingetPin):
            installed = install_pinned_winget(
                spec, authorized=True, online=online,
            )
            steps.append(ProvisionStep(action.name, action.action, installed.state))
            if installed.state != "INSTALLED_NOT_HEALTHCHECKED":
                return ProvisionExecution(
                    "PARTIAL_OR_BLOCKED", tuple(steps),
                    ("Windows package manager could not provision " + action.name,),
                )
            continue
        existing = inspect_versioned_zip(spec, install_root=install_root)
        if existing.status == "BLOCKED_INVALID":
            steps.append(ProvisionStep(action.name, action.action, "BLOCKED_INVALID"))
            return ProvisionExecution(
                "PARTIAL_OR_BLOCKED", tuple(steps),
                ("Existing pinned payload is corrupt or unsafe: " + action.name,),
            )
        if existing.status == "VERIFIED_PAYLOAD":
            status = "STAGED_VERIFIED"
            if spec.executable is not None:
                health = check_staged_binary(spec, install_root=install_root)
                if health.state != "BINARY_VERSION_PROBED":
                    steps.append(ProvisionStep(action.name, action.action, health.state))
                    return ProvisionExecution(
                        "PARTIAL_OR_BLOCKED", tuple(steps),
                        ("Native binary health check failed: " + action.name,),
                    )
                status = "STAGED_NATIVE_PROBED"
            steps.append(ProvisionStep(action.name, action.action, status))
            continue
        try:
            published = install_versioned_zip(
                spec, install_root=install_root, cache_root=cache_root,
                authorized=True, cancelled=cancelled, progress=progress,
            )
        except (InstallError, DownloadError, OSError) as exc:
            steps.append(ProvisionStep(action.name, action.action, "BLOCKED"))
            return ProvisionExecution(
                "PARTIAL_OR_BLOCKED", tuple(steps),
                ("Dependency staging failed for " + action.name
                 + " (" + type(exc).__name__ + ")",),
            )
        status = "STAGED_" + published.status
        if spec.executable is not None:
            health = check_staged_binary(spec, install_root=install_root)
            if health.state != "BINARY_VERSION_PROBED":
                steps.append(ProvisionStep(action.name, action.action, health.state))
                return ProvisionExecution(
                    "PARTIAL_OR_BLOCKED", tuple(steps),
                    ("Native binary health check failed: " + action.name,),
                )
            status = "STAGED_NATIVE_PROBED"
        steps.append(ProvisionStep(action.name, action.action, status))

    # No native PATH registration, service health, model test or release gate
    # has been satisfied. Even the all-REUSE case must benchmark its model.
    return ProvisionExecution("PAYLOADS_STAGED_NOT_VALIDATED", tuple(steps), ())

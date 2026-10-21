"""Execute only necessary, release-pinned dependency staging actions.

This is the bridge between the measured inventory and versioned ZIP installer.
It never modifies the PATH, starts a downloaded binary or claims an Ollama
model is ready. The packaged Windows setup remains the authority for consent,
ACLs, native service registration and post-install health checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .inventory import Inventory
from .provision_plan import make_plan
from .staged_install import (InstallError, PinnedZip, inspect_versioned_zip,
                             install_versioned_zip)
from .downloads import DownloadError


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
    snapshot: Inventory, pins: tuple[PinnedZip, ...], *,
    online: bool, authorized: bool,
    install_root: Path, cache_root: Path,
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
    manifest: dict[str, PinnedZip] = {}
    for spec in pins:
        if not isinstance(spec, PinnedZip):
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
        spec = manifest[action.name]
        existing = inspect_versioned_zip(spec, install_root=install_root)
        if existing.status == "BLOCKED_INVALID":
            steps.append(ProvisionStep(action.name, action.action, "BLOCKED_INVALID"))
            return ProvisionExecution(
                "PARTIAL_OR_BLOCKED", tuple(steps),
                ("Existing pinned payload is corrupt or unsafe: " + action.name,),
            )
        if existing.status == "VERIFIED_PAYLOAD":
            steps.append(ProvisionStep(action.name, action.action, "STAGED_VERIFIED"))
            continue
        try:
            published = install_versioned_zip(
                spec, install_root=install_root, cache_root=cache_root,
                authorized=True,
            )
        except (InstallError, DownloadError, OSError) as exc:
            steps.append(ProvisionStep(action.name, action.action, "BLOCKED"))
            return ProvisionExecution(
                "PARTIAL_OR_BLOCKED", tuple(steps),
                ("Dependency staging failed for " + action.name
                 + " (" + type(exc).__name__ + ")",),
            )
        steps.append(ProvisionStep(action.name, action.action,
                                   "STAGED_" + published.status))

    # No native PATH registration, service health, model test or release gate
    # has been satisfied. Even the all-REUSE case must benchmark its model.
    return ProvisionExecution("PAYLOADS_STAGED_NOT_VALIDATED", tuple(steps), ())

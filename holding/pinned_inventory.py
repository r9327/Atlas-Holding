"""Reconcile a fresh PATH inventory with release-pinned installed executables.

The package's requested version and the real executable must both pass health
checks. An untrusted file in a standard-looking folder is never sufficient.
No installation, environment modification, or background work occurs here.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .inventory import Component, Inventory, _ollama
from .managed_toolchain import resolve_managed_tool
from .staged_install import PinnedZip
from .windows_package_manager import WingetPin
from .winget_binary import discover_winget_binary


def restore_pinned_inventory(
    snapshot: Inventory, pins: tuple[PinnedZip | WingetPin, ...], *,
    install_root: Path,
) -> Inventory:
    """Recognize tools again on the second and every subsequent app launch.

    Never overwrite an already healthy PATH component, downgrade a broken
    program by declaration, or identify a server without a real API check.
    """
    if not isinstance(snapshot, Inventory) or not isinstance(pins, tuple):
        raise ValueError("Inventory and trusted pins required")
    if not isinstance(install_root, Path) or not install_root.is_absolute():
        raise ValueError("Absolute installation root required")
    updates: dict[str, Component | tuple] = {}
    for pin in pins:
        if not isinstance(pin, (PinnedZip, WingetPin)):
            raise ValueError("Untrusted release pin")
        pin.validate()
        name = pin.component
        if name not in {"git", "opencode", "ollama"}:
            continue
        original: Component = getattr(snapshot, name)
        if original.status == "FOUND_VALID":
            continue
        if original.status not in {"NOT_FOUND", "FOUND_BROKEN"}:
            continue
        if isinstance(pin, PinnedZip):
            native = resolve_managed_tool(pin, install_root=install_root)
            if native.state != "VERIFIED_EXECUTABLE":
                continue
            found = Component(
                "FOUND_VALID", native.executable, native.expected_version,
                "Pinned Holding-managed executable",
            )
        else:
            native = discover_winget_binary(pin)
            if native.state != "EXACT_BINARY_VERIFIED":
                continue
            found = Component(
                "FOUND_VALID", native.executable, native.version,
                "Native pinned WinGet executable",
            )
        if name == "ollama":
            service, models = _ollama(found)
            if service.status == "FOUND_VALID":
                updates["ollama"] = service
                updates["models"] = models
            elif original.status == "NOT_FOUND":
                # Retain the executable for a targeted restart, without
                # claiming the Ollama API or any model has passed inspection.
                updates["ollama"] = Component(
                    "FOUND_BROKEN", path=found.path, version=found.version,
                    reason="Ollama installed but local API is not healthy",
                )
        else:
            updates[name] = found
    return replace(snapshot, **updates) if updates else snapshot

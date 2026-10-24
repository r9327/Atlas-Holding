"""Fresh Ollama API inventory for pinned executables outside process PATH.

A WinGet install or an Atlas-managed ZIP can start its daemon before Windows
refreshes a running GUI's PATH. Verify its exact binary release and live API
rather than trusting the installer exit code or discarding the model list.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .inventory import Component, Inventory, _ollama, scan
from .managed_toolchain import resolve_managed_tool
from .staged_install import PinnedZip
from .windows_package_manager import WingetPin
from .winget_binary import discover_winget_binary


def scan_ollama_with_pins(
    install_root: Path, pins: tuple[PinnedZip | WingetPin, ...],
) -> Inventory:
    snapshot = scan(install_root)
    if snapshot.ollama.status == "FOUND_VALID":
        return snapshot
    for pin in pins:
        if pin.component != "ollama":
            continue
        if isinstance(pin, WingetPin):
            checked = discover_winget_binary(pin)
            if checked.state != "EXACT_BINARY_VERIFIED":
                break
            binary = Component("FOUND_VALID", checked.executable, checked.version)
        elif isinstance(pin, PinnedZip):
            checked = resolve_managed_tool(pin, install_root=install_root)
            if checked.state != "VERIFIED_EXECUTABLE":
                break
            binary = Component(
                "FOUND_VALID", checked.executable, checked.expected_version,
            )
        else:
            break
        service, models = _ollama(binary)
        if service.status != "FOUND_VALID":
            break
        return replace(snapshot, ollama=service, models=models)
    return snapshot

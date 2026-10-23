"""Resolve the Holding's own immutable ZIP tools without editing Windows PATH.

Only trusted release pins and the bytes actually installed under the chosen
volume may become an executable binding. Bindings are read-only snapshots:
an execution boundary must reverify immediately before launching the tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .native_health import check_staged_binary
from .staged_install import PinnedZip, inspect_versioned_zip


@dataclass(frozen=True)
class ManagedTool:
    component: str
    state: str
    executable: str = ""
    expected_version: str = ""


def resolve_managed_tool(
    pin: PinnedZip, *, install_root: Path,
) -> ManagedTool:
    """Never return a runnable path unless all pinned bytes + version pass."""
    if not isinstance(pin, PinnedZip):
        raise ValueError("A trusted pinned ZIP is required")
    pin.validate()
    if not isinstance(install_root, Path) or not install_root.is_absolute():
        raise ValueError("An absolute managed installation root is required")
    if pin.component not in {"git", "opencode", "ollama"}:
        return ManagedTool(pin.component, "UNSUPPORTED_COMPONENT")
    if pin.executable is None:
        return ManagedTool(pin.component, "NO_NATIVE_PROBE_CONFIGURED")
    checked = check_staged_binary(pin, install_root=install_root)
    if checked.state != "BINARY_VERSION_PROBED":
        return ManagedTool(pin.component, checked.state)
    inspected = inspect_versioned_zip(pin, install_root=install_root)
    if inspected.status != "VERIFIED_PAYLOAD":
        return ManagedTool(pin.component, "CHANGED_AFTER_NATIVE_PROBE")
    target = inspected.directory.joinpath(*pin.executable.split("/"))
    # The native probe already rejected symlinked paths, junctions and hardlinks.
    # A later launcher must still revalidate because filesystem data can change.
    return ManagedTool(pin.component, "VERIFIED_EXECUTABLE",
                       str(target), pin.expected_version or "")

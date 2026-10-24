"""Strict runtime identity for first-party MCP inside the installed companion.

This authorizes *only* a pinned local CPython companion in the installer's
versioned mcp-python folder. It never authorizes arbitrary executables, agent
arguments or third-party MCP tools.
"""
from __future__ import annotations

from pathlib import Path
import sys

from .mcp_local_inventory import PinnedLocalPlugin, inspect_pinned_plugin
from .mcp_package_pin import McpPackagePin, McpPackagePinError


class McpCompanionTrustError(ValueError):
    pass


def verify_internal_hub_runtime(
    *, interpreter_pin: PinnedLocalPlugin,
    package_root: Path,
    package_pin: McpPackagePin | None,
) -> str:
    if (type(interpreter_pin) is not PinnedLocalPlugin
            or not isinstance(package_root, Path) or not package_root.is_absolute()
            or package_root.is_symlink() or ".." in package_root.parts
            or not package_root.is_dir()):
        raise McpCompanionTrustError("Untrusted internal MCP package root")
    try:
        root = package_root.resolve(strict=True)
        executable = interpreter_pin.executable.resolve(strict=True)
        actual = Path(sys.executable).resolve(strict=True)
        code = root / "holding"
        if (root != package_root
                or interpreter_pin.executable.is_symlink()
                or not executable.is_file()
                or code.is_symlink() or not code.is_dir()):
            raise McpCompanionTrustError("Linked or unavailable installed MCP files")
        local_root = Path(__file__).resolve().parent.parent
        if root == local_root and executable == actual:
            if package_pin is not None and not package_pin.verify(root):
                raise McpCompanionTrustError("In-repo MCP code digest changed")
            mode = "IN_PROCESS_RUNTIME"
        elif (type(package_pin) is McpPackagePin
                and executable.parent == root
                and executable.name.lower() == "python.exe"
                and root.parent.name == "mcp-python"
                and root.name.startswith("python-embedded-")
                and len(root.name) == len("python-embedded-") + 16
                and all(c in "0123456789abcdef"
                        for c in root.name[len("python-embedded-"):])
                and not root.parent.is_symlink()
                and package_pin.verify(root)):
            mode = "INSTALLED_PINNED_COMPANION"
        else:
            raise McpCompanionTrustError("Unapproved MCP binary/package pairing")
        if inspect_pinned_plugin(interpreter_pin).binary_state != "BINARY_VERIFIED":
            raise McpCompanionTrustError("Pinned CPython executable is not verified")
        return mode
    except (OSError, McpPackagePinError) as exc:
        raise McpCompanionTrustError("Installed MCP runtime cannot be verified") from exc

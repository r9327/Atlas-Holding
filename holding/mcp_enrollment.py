"""Installer-only MCP v5 enrollment, with external expected hashes.

An installed user configuration must not self-attest executable or module
integrity: the expected digests must come from *trusted installer/release*
metadata, not from a hash of an arbitrary existing local installation.
This module does not download, start, or repair a Python interpreter.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

from .daemon_entry import Installation
from .mcp_local_inventory import PinnedLocalPlugin, inspect_pinned_plugin
from .mcp_package_pin import McpPackagePin, McpPackagePinError
from .mcp_windows_acl import require_private_windows_path


MAX_INSTALL_CONFIG_BYTES = 32 * 1024


class McpEnrollmentError(ValueError):
    pass


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise McpEnrollmentError("Duplicate installer configuration key")
        result[key] = value
    return result


@dataclass(frozen=True)
class McpEnrollmentPlan:
    source: Path
    source_sha256: str
    payload: bytes
    sha256: str
    pinned_module_count: int


def prepare_v5_mcp_enrollment(
    *, source_config: Path, installed_package_root: Path,
    interpreter_path: Path, trusted_interpreter_sha256: str,
    trusted_module_sha256: dict[str, str], timeout_seconds: float = 3.0,
) -> McpEnrollmentPlan:
    """Prepare deterministic, complete v5 config without touching disk.

    The caller must have obtained interpreter and module hashes through
    independent, authenticated distribution metadata.
    """
    if getattr(sys, "frozen", False):
        raise McpEnrollmentError("MCP needs a real CPython interpreter, not a frozen EXE")
    if (not isinstance(source_config, Path) or not source_config.is_absolute()
            or not source_config.is_file() or source_config.is_symlink()
            or not isinstance(installed_package_root, Path)
            or not installed_package_root.is_absolute()
            or not isinstance(interpreter_path, Path)
            or not interpreter_path.is_absolute()
            or type(timeout_seconds) not in (int, float)
            or isinstance(timeout_seconds, bool)
            or not .25 <= timeout_seconds <= 10):
        raise McpEnrollmentError("Trusted installer paths or bounds invalid")
    try:
        if installed_package_root.resolve(strict=True) != Path(__file__).resolve().parent.parent:
            raise McpEnrollmentError("Installer must enroll its actual Python package")
        exe = interpreter_path.resolve(strict=True)
        if (interpreter_path.is_symlink()
                or exe != Path(sys.executable).resolve(strict=True)):
            raise McpEnrollmentError("Installer must enroll its running CPython")
        pins = McpPackagePin.from_manifest(trusted_module_sha256)
        if not pins.verify(installed_package_root):
            raise McpEnrollmentError("Python module digests do not match release evidence")
        interpreter = PinnedLocalPlugin(
            "atlas-holding-internal", "1.0", exe,
            trusted_interpreter_sha256)
        if inspect_pinned_plugin(interpreter).binary_state != "BINARY_VERIFIED":
            raise McpEnrollmentError("Interpreter does not match release evidence")
        if os.name == "nt":
            require_private_windows_path(source_config)
            require_private_windows_path(exe)
            package = installed_package_root / "holding"
            require_private_windows_path(package)
            for name, _sha in pins.entries:
                require_private_windows_path(package / name, check_parent=False)
    except (McpPackagePinError, OSError, ValueError) as exc:
        if isinstance(exc, McpEnrollmentError):
            raise
        raise McpEnrollmentError("Cannot verify signed installer inventory") from exc
    try:
        raw = source_config.read_bytes()
        if not 1 <= len(raw) <= MAX_INSTALL_CONFIG_BYTES:
            raise McpEnrollmentError("Installer config length is invalid")
        document = json.loads(raw, object_pairs_hook=_unique_pairs)
        if (type(document) is not dict
                or type(document.get("schema")) is not int
                or document["schema"] not in (3, 4, 5)):
            raise McpEnrollmentError("Upgrade requires a supported GUI installer config")
        if document["schema"] in (4, 5) and document.get("mcp") is not None:
            raise McpEnrollmentError("Refusing to replace existing MCP enrollment")
        # Existing service, paths, GitHub authorization and dashboard must
        # already pass the installer's strict independent validation.
        Installation.load(source_config)
        if source_config.read_bytes() != raw:
            raise McpEnrollmentError("Installer configuration changed during enrollment")
    except McpEnrollmentError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise McpEnrollmentError("Cannot validate existing installer configuration") from exc
    document["schema"] = 5
    document["mcp"] = {
        "interpreter": str(exe),
        "interpreter_sha256": interpreter.expected_sha256,
        "response_timeout": float(timeout_seconds),
        "module_sha256": dict(pins.entries),
    }
    prepared = (json.dumps(document, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True) + "\n").encode("utf-8")
    if not 1 <= len(prepared) <= MAX_INSTALL_CONFIG_BYTES:
        raise McpEnrollmentError("Pinned installation exceeds config size budget")
    return McpEnrollmentPlan(
        source=source_config,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        payload=prepared,
        sha256=hashlib.sha256(prepared).hexdigest(),
        pinned_module_count=len(pins.entries),
    )


def create_new_v5_config(*, plan: McpEnrollmentPlan, destination: Path) -> Path:
    """Write a new, exclusive config: never overwrite source or another file."""
    if (type(plan) is not McpEnrollmentPlan or not isinstance(destination, Path)
            or not destination.is_absolute() or destination.is_symlink()
            or destination.parent.resolve() != plan.source.parent.resolve()
            or destination == plan.source or not destination.name.endswith(".json")
            or hashlib.sha256(plan.payload).hexdigest() != plan.sha256
            or hashlib.sha256(plan.source.read_bytes()).hexdigest() != plan.source_sha256):
        raise McpEnrollmentError("Invalid exclusive installer destination")
    if os.name == "nt":
        require_private_windows_path(destination.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    created = False
    try:
        descriptor = os.open(destination, flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            output.write(plan.payload)
            output.flush()
            os.fsync(output.fileno())
        if os.name == "nt":
            require_private_windows_path(destination)
        Installation.load(destination)
        if not hmac.compare_digest(
                hashlib.sha256(destination.read_bytes()).hexdigest(),
                plan.sha256):
            raise McpEnrollmentError("Created installer configuration changed")
        return destination
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise

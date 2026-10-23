"""First-run Windows provisioning wizard using the Python standard library.

The wizard never evaluates remote code, invents a model benchmark, changes the
existing PATH, or claims the application is ready solely because ZIPs unpacked.
The trusted packaged host pins the release manifest before launching this UI.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
import shutil
from pathlib import Path
from queue import Empty, Queue
import threading
from typing import Callable
from urllib.request import Request, build_opener, ProxyHandler

from .bootstrap_assets_cli import read_pinned_manifest
from .inventory import Component, Inventory, scan
from .provision_plan import make_plan
from .provision_runtime import (ProvisionExecution, ProvisionStep,
                                stage_required_dependencies)
from .staged_install import PinnedZip, inspect_versioned_zip, _no_linked_ancestors
from .windows_package_manager import WingetPin
from .managed_toolchain import resolve_managed_tool
from .version_evidence import matches_component_version
from .ollama_setup import ensure_selected_model_volume
from .ollama_storage import read_model_directory
from .windows_autostart import configure_logon_resume
from .windows_ollama_environment import configure_user_models, undo_created_user_models
from .model_volume import inspect_model_volume
from .winget_binary import discover_winget_binary
from .pinned_inventory import restore_pinned_inventory
from .windows_runtime_payload import (
    install_pinned_runtime_pair, verify_runtime_payload, RuntimePayloadError,
    install_pinned_adapter, verify_pinned_adapter,
)
from .windows_engine_enrollment import (
    EngineEnrollmentError, EngineEnrollmentRequest,
    protect_approved_install_root, prepare_protected_engine_roots,
    enroll_pinned_engine, launch_enrolled_engine,
)
from .windows_runtime_acl import validate_runtime_acl, secure_runtime_acl
from .embedded_python_stage import EmbeddedPythonRelease, EmbeddedRuntimeStage, stage_pinned_embedded_python, verify_staged_embedded_python
from .windows_model_enrollment import (
    discover_local_models, enroll_local_model, LocalModelEnrollmentError,
)


@dataclass(frozen=True)
class SetupSummary:
    phase: str
    status: str
    os_name: str
    architecture: str
    gpu: str
    ram_bytes: int | None
    free_bytes: int
    download_upper_bound_bytes: int
    dependency_actions: tuple[tuple[str, str, str], ...]
    installed_models: int
    blockers: tuple[str, ...]
    unestimated_package_downloads: int = 0
    model_volume_free_bytes: int | None = None
    model_volume_status: str = "NOT_CHECKED"


def detect_online(*, timeout: float = 3.0) -> bool:
    """Best-effort HTTPS probe; failure is offline/unknown, never consent."""
    if not 0 < timeout <= 15:
        raise ValueError("Invalid network probe deadline")
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(
            Request("https://github.com/", method="HEAD"), timeout=timeout,
        ) as response:
            return 200 <= response.status < 400
    except (OSError, ValueError):
        return False


def preflight_setup(
    *, manifest: Path, manifest_sha256: str,
    install_root: Path, cache_root: Path, online: bool,
    model_root: Path | None = None,
) -> tuple[SetupSummary, tuple[PinnedZip | WingetPin, ...]]:
    """Read-only inventory, compatibility checks and conservative download sizes."""
    if not install_root.is_absolute() or not cache_root.is_absolute():
        raise ValueError("Installation folders must be absolute")
    pins = read_pinned_manifest(manifest, expected_sha256=manifest_sha256)
    snapshot = restore_pinned_inventory(
        scan(install_root), pins, install_root=install_root,
    )
    plan = make_plan(snapshot, online=online)
    pin_map = {item.component: item for item in pins}
    dependency_actions = []
    upper_bound = 0
    winget_unknown = 0
    blockers = list(plan.blockers)
    observed_volume = (inspect_model_volume(model_root)
                       if model_root is not None else None)
    if observed_volume is not None and observed_volume.status != "VOLUME_AVAILABLE":
        blockers.append("Selected AI models disk is not usable: " +
                        observed_volume.status)
    if plan.state == "INSPECTION_REQUIRED":
        blockers.append("An installed dependency requires manual inspection")
    for action in plan.actions:
        detail = action.reason
        if action.action in {"INSTALL", "REPAIR"}:
            spec = pin_map.get(action.name)
            if spec is None:
                blockers.append("Missing release pin for " + action.name)
            elif isinstance(spec, WingetPin):
                detail = "WINGET_VERSION_PINNED"
                winget_unknown += 1
            else:
                observed = inspect_versioned_zip(spec, install_root=install_root)
                detail = observed.status
                if observed.status == "BLOCKED_INVALID":
                    blockers.append("Corrupt pinned payload for " + action.name)
                elif observed.status == "NOT_INSTALLED":
                    upper_bound += spec.download_bytes
        dependency_actions.append((action.name, action.action, detail))
    state = "BLOCKED" if blockers else (
        "CONSENT_REQUIRED" if any(
            item.action in {"INSTALL", "REPAIR", "START_SERVICE"} for item in plan.actions
        ) else "BENCHMARK_REQUIRED"
    )
    return SetupSummary(
        "PREFLIGHT", state, snapshot.os_name, snapshot.os_architecture,
        snapshot.gpu, snapshot.ram_total_bytes,
        snapshot.installation_disk_free_bytes, upper_bound,
        tuple(dependency_actions), len(snapshot.models), tuple(blockers),
        winget_unknown,
        observed_volume.free_bytes if observed_volume else None,
        observed_volume.status if observed_volume else "NOT_CHECKED",
    ), pins


def assess_post_install(
    result: ProvisionExecution, snapshot: Inventory,
    pins: tuple[PinnedZip | WingetPin, ...],
    *, install_root: Path | None = None,
) -> ProvisionExecution:
    """Re-measure after setup; a package-manager exit code is never runtime QA.

    Validate pinned component versions for newly provisioned tools. A healthy
    reused executable is intentionally not downgraded to meet a release pin.
    """
    if result.state != "PAYLOADS_STAGED_NOT_VALIDATED":
        return result
    known = {
        "git": snapshot.git, "ollama": snapshot.ollama,
        "opencode": snapshot.opencode, "python_runtime": snapshot.python,
    }
    pinned = {spec.component: spec for spec in pins}
    mutating = {step.name for step in result.steps
                if step.action in {"INSTALL", "REPAIR"}}
    steps = list(result.steps)
    blockers: list[str] = []
    for name, component in known.items():
        spec = pinned.get(name)
        # ZIP executables live under the selected Holding volume, not Windows
        # PATH. Resolve the verified binary only for components just staged.
        # An executable version probe is enough for Git/OpenCode discovery,
        # never for Ollama: the local Ollama HTTP API must also be healthy.
        if (name in {"git", "opencode"} and name in mutating
                and isinstance(spec, PinnedZip) and install_root is not None):
            managed = resolve_managed_tool(spec, install_root=install_root)
            if managed.state == "VERIFIED_EXECUTABLE":
                component = Component(
                    "FOUND_VALID", path=managed.executable,
                    version=managed.expected_version,
                    reason="Holding-managed, SHA-verified executable",
                )
                steps.append(ProvisionStep(name, "MANAGED_BINDING",
                                           "VERIFIED_EXECUTABLE"))
            elif component.status != "FOUND_VALID":
                steps.append(ProvisionStep(name, "MANAGED_BINDING", managed.state))
        if (name in {"git", "opencode"} and name in mutating
                and isinstance(spec, WingetPin)
                and component.status != "FOUND_VALID"):
            winget_binary = discover_winget_binary(spec)
            steps.append(ProvisionStep(name, "WINGET_BINDING",
                                       winget_binary.state))
            if winget_binary.state == "EXACT_BINARY_VERIFIED":
                component = Component(
                    "FOUND_VALID", path=winget_binary.executable,
                    version=winget_binary.version,
                    reason="Native user-scope WinGet executable probed",
                )
        if component.status != "FOUND_VALID":
            steps.append(ProvisionStep(name, "POST_INSTALL_HEALTH", component.status))
            blockers.append("Post-install health check failed: " + name)
            continue
        if name in mutating and spec is not None:
            # A publisher ZIP asset tag may differ from the executable's
            # --version output (MinGit 2.56.0.2 reports Git 2.56.0).
            # Native ZIP evidence has its own explicitly signed version pin.
            expected_native_version = (
                spec.expected_version
                if isinstance(spec, PinnedZip) and spec.expected_version
                else spec.version
            )
            if not matches_component_version(
                    name, expected_native_version, component.version):
                steps.append(ProvisionStep(name, "POST_INSTALL_HEALTH", "VERSION_MISMATCH"))
                blockers.append("Post-install version mismatch: " + name)
                continue
        steps.append(ProvisionStep(name, "POST_INSTALL_HEALTH", "FOUND_VALID"))
    return ProvisionExecution(
        "POST_INSTALL_RECHECK_REQUIRED" if blockers else
        "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING",
        tuple(steps), tuple(blockers),
    )


def stage_embedded_mcp_companion(
    *, install_root: Path, archive: Path, trusted_source_root: Path,
    pin: EmbeddedPythonRelease, approved: bool,
) -> EmbeddedRuntimeStage:
    """Install an opt-in, byte-verified CPython under the approved install root.

    Does not activate MCP or treat a fabricated local receipt as a release
    signature; upstream archive and source hashes must be independently pinned.
    """
    from .windows_engine_enrollment import _require_native_windows
    if approved is not True or type(pin) is not EmbeddedPythonRelease:
        raise RuntimePayloadError("Explicit release-pinned MCP approval required")
    _require_native_windows()
    if (not isinstance(install_root, Path) or not install_root.is_absolute()
            or install_root.is_symlink()
            or install_root.resolve(strict=True) != install_root):
        raise RuntimePayloadError("Canonical chosen installation root required")
    _no_linked_ancestors(install_root)
    validate_runtime_acl(install_root)
    destination = install_root / "mcp-python"
    created = False
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise RuntimePayloadError("Unsafe installed CPython companion directory")
        validate_runtime_acl(destination)
    else:
        destination.mkdir()
        created = True
    staged = None
    try:
        if created:
            secure_runtime_acl(destination)
        staged = stage_pinned_embedded_python(
            archive=archive, approved_package_root=trusted_source_root,
            target_parent=destination, pin=pin)
        secure_runtime_acl(staged.directory)
        validate_runtime_acl(staged.directory)
        validate_runtime_acl(staged.python_exe)
        validate_runtime_acl(staged.directory / "holding")
        for filename, _digest in staged.source_sha256:
            validate_runtime_acl(staged.directory / "holding" / filename)
        result = verify_staged_embedded_python(
            directory=staged.directory, pin=pin)
        if result.interpreter_sha256 != staged.interpreter_sha256:
            raise RuntimePayloadError("Embedded Python executable changed")
        return result
    except BaseException:
        # Roll back only directories created by this call.
        if staged is not None and staged.directory.exists():
            shutil.rmtree(staged.directory)
        if created and destination.exists() and not any(destination.iterdir()):
            destination.rmdir()
        raise


def execute_setup(
    *, manifest: Path, manifest_sha256: str, install_root: Path,
    cache_root: Path, online: bool, approved: bool,
    model_root: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    runtime_root: Path | None = None,
    runtime_pins: dict[str, str] | None = None,
    adapter_source: Path | None = None,
    adapter_sha256: str | None = None,
    embedded_python_archive: Path | None = None,
    embedded_source_root: Path | None = None,
    embedded_python_pin: EmbeddedPythonRelease | None = None,
) -> tuple[SetupSummary, ProvisionExecution | None]:
    """Recheck every premise before any authorized staging operation."""
    if (runtime_root is None) != (runtime_pins is None):
        raise ValueError("Complete pinned runtime pair required")
    companion = (embedded_python_archive, embedded_source_root, embedded_python_pin)
    if any(item is not None for item in companion) and (
            any(item is None for item in companion) or runtime_root is None):
        raise ValueError("Complete embedded MCP Python and Holding release pins required")
    if (adapter_source is None) != (adapter_sha256 is None):
        raise ValueError("Complete bundled agent source and hash required")
    if adapter_source is not None and runtime_root is None:
        raise ValueError("Bundled agent needs pinned engine and viewer")
    if adapter_source is not None:
        verify_pinned_adapter(adapter_source, adapter_sha256)
    if runtime_root is not None:
        # Refuse mutations before checking the two real binaries.
        verify_runtime_payload(runtime_root, runtime_pins)
    if model_root is not None:
        if not isinstance(model_root, Path) or not model_root.is_absolute():
            raise ValueError("Absolute model-storage destination required")
        _no_linked_ancestors(model_root)
    report, pins = preflight_setup(
        manifest=manifest, manifest_sha256=manifest_sha256,
        install_root=install_root, cache_root=cache_root, online=online,
        model_root=model_root,
    )
    if report.status == "BLOCKED":
        return report, None
    if not approved:
        return report, None
    # Fresh inventory is intentionally acquired by the privileged staging
    # boundary. No selected model or network-dependent service is fabricated.
    snapshot = restore_pinned_inventory(
        scan(install_root), pins, install_root=install_root,
    )
    # WinGet's Ollama Desktop may auto-start before we can launch our own
    # daemon. The official Windows contract requires HKCU OLLAMA_MODELS.
    # Apply this *only* for a new user-scope Ollama installation, under the
    # explicit setup authorization. Never overwrite an existing other path.
    chosen_user_models = None
    if (model_root is not None and snapshot.ollama.status == "NOT_FOUND"
            and any(isinstance(x, WingetPin) and x.component == "ollama"
                    for x in pins)):
        chosen_user_models = configure_user_models(
            model_root, authorized=True,
        )
        if chosen_user_models.status not in {
            "USER_MODELS_CREATED", "USER_MODELS_ALREADY_MATCH",
        }:
            return report, ProvisionExecution(
                "PARTIAL_OR_BLOCKED",
                (ProvisionStep(
                    "ollama", "USER_MODEL_ENVIRONMENT",
                    chosen_user_models.status,
                ),),
                ("Cannot configure Ollama user model volume",),
            )
    result = stage_required_dependencies(
        snapshot, pins, online=online, authorized=True,
        install_root=install_root, cache_root=cache_root,
        cancelled=cancelled, progress=progress, model_root=model_root,
    )
    if chosen_user_models is not None:
        status = chosen_user_models.status
        if (status == "USER_MODELS_CREATED"
                and result.state != "PAYLOADS_STAGED_NOT_VALIDATED"
                and not any(
                    step.name == "ollama" and
                    step.result == "INSTALLED_NOT_HEALTHCHECKED"
                    for step in result.steps
                )):
            status = undo_created_user_models(model_root)
        result = ProvisionExecution(
            result.state, result.steps + (
                ProvisionStep("ollama", "USER_MODEL_ENVIRONMENT", status),
            ), result.blockers,
        )
    if result.state == "PAYLOADS_STAGED_NOT_VALIDATED":
        observed = restore_pinned_inventory(
            scan(install_root), pins, install_root=install_root,
        )
        if model_root is not None:
            result, observed = ensure_selected_model_volume(
                result, observed, install_root=install_root,
                model_root=model_root, pins=pins, cancelled=cancelled,
            )
        result = assess_post_install(
            result, observed, pins, install_root=install_root,
        )
    if runtime_root is not None and result.state == (
            "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING"):
        try:
            # Root ACL consent is part of the explicit setup authorization
            # only for a bundled, release-pinned adapter.
            if adapter_source is not None:
                protect_approved_install_root(install_root, approved=True)
            installed = install_pinned_runtime_pair(
                runtime_root, install_root, runtime_pins, approved=True,
                cancelled=cancelled,
            )
            stage_steps = result.steps + (
                ProvisionStep(
                    "holding-runtime", "STAGE_VERIFIED_PAIR",
                    installed.state,
                ),
            )
            if adapter_source is not None:
                agent_result = install_pinned_adapter(
                    adapter_source, install_root, adapter_sha256,
                    approved=True,
                )
                stage_steps += (
                    ProvisionStep(
                        "holding-adapter", "STAGE_VERIFIED_AGENT", agent_result,
                    ),
                )
            result = ProvisionExecution(
                "RUNTIME_STAGED_ENGINE_CONFIG_PENDING",
                stage_steps, result.blockers,
            )
        except (RuntimePayloadError, EngineEnrollmentError, OSError, ValueError):
            result = ProvisionExecution(
                "PARTIAL_OR_BLOCKED",
                result.steps + (
                    ProvisionStep(
                        "holding-runtime", "STAGE_VERIFIED_PAIR",
                        "RUNTIME_STAGE_FAILED",
                    ),
                ),
                result.blockers + ("Pinned runtime staging failed",),
            )
    if (embedded_python_pin is not None and result is not None
            and result.state == "RUNTIME_STAGED_ENGINE_CONFIG_PENDING"):
        try:
            staged = stage_embedded_mcp_companion(
                install_root=install_root, archive=embedded_python_archive,
                trusted_source_root=embedded_source_root,
                pin=embedded_python_pin, approved=True)
            result = ProvisionExecution(
                result.state,
                result.steps + (ProvisionStep(
                    "mcp-python", "STAGE_VERIFIED_EMBEDDED",
                    staged.status),), result.blockers,
            )
        except (OSError, ValueError, RuntimePayloadError):
            result = ProvisionExecution(
                "PARTIAL_OR_BLOCKED",
                result.steps + (ProvisionStep(
                    "mcp-python", "STAGE_VERIFIED_EMBEDDED",
                    "MCP_COMPANION_STAGE_FAILED"),),
                result.blockers + ("MCP companion staging failed",),
            )
    return report, result



def configure_and_launch_engine(
    *, install_root: Path, atlas_root: Path, runtime_pins: dict[str, str],
    adapter_sha256: str, approved: bool, launch_approved: bool,
    model_name: str | None = None,
):
    """Explicit first-run GUI handoff. No model/adapter is silently invented.

    Only an agent pinned by the packaged setup at agents/AtlasHoldingAgent.exe
    may be enrolled. A copied EXE is not evidence of a working agent; actual
    engine health and the complete roadmap remain unverified after launch.
    """
    import re
    from .windows_engine_enrollment import _require_native_windows
    if approved is not True or type(launch_approved) is not bool:
        raise EngineEnrollmentError("Explicit engine configuration consent required")
    if (not isinstance(install_root, Path) or not install_root.is_absolute()
            or not isinstance(atlas_root, Path) or not atlas_root.is_absolute()
            or not isinstance(adapter_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", adapter_sha256) is None
            or not isinstance(runtime_pins, dict)
            or set(runtime_pins) != {
                "AtlasHoldingControlCenter.exe", "AtlasHoldingEngine.exe",
            }):
        raise EngineEnrollmentError("Trusted first-run installation inputs required")
    _require_native_windows()
    if (install_root.is_symlink() or install_root.resolve(strict=True) != install_root
            or atlas_root.is_symlink()
            or atlas_root.resolve(strict=True) != atlas_root
            or install_root == atlas_root or install_root in atlas_root.parents
            or atlas_root in install_root.parents):
        raise EngineEnrollmentError("Atlas checkout and installation must be isolated")
    # Exact versioned pair published by install_pinned_runtime_pair(), not
    # arbitrary executable paths supplied by a roadmap or UI.
    version = (runtime_pins["AtlasHoldingControlCenter.exe"][:16] + "-"
               + runtime_pins["AtlasHoldingEngine.exe"][:16])
    runtime = install_root / "runtime" / version
    verify_runtime_payload(runtime, runtime_pins)
    agent_folder = install_root / "agents"
    agent = agent_folder / "AtlasHoldingAgent.exe"
    if (agent_folder.is_symlink() or agent.is_symlink()
            or not agent.is_file() or not agent_folder.is_dir()
            or agent.resolve(strict=True) != agent):
        raise EngineEnrollmentError("Pinned installed agent adapter unavailable")
    try:
        verify_pinned_adapter(agent, adapter_sha256)
    except RuntimePayloadError as exc:
        raise EngineEnrollmentError(
            "Installed agent does not match trusted release pin") from exc
    # Reject an absent/unselected model BEFORE modifying any ACL or folder.
    # The model identity is verified again during pin publication to detect
    # changes between inventory and enrollment.
    if not model_name:
        raise EngineEnrollmentError("An installed Ollama model must be selected")
    if model_name not in dict(discover_local_models()):
        raise EngineEnrollmentError("The chosen Ollama model is not installed")
    # Root ACL modifications are separately approved in the first-run dialog.
    # They are never attempted against a drive or user profile root.
    protect_approved_install_root(install_root, approved=True)
    for path in (agent_folder, agent):
        validate_runtime_acl(path)
    # Unrelated user data is never silently ACL-repaired. Data roots may be
    # added only under the already-protected approved installation root.
    prepare_protected_engine_roots(install_root, approved=True)
    for name in ("workspaces", "health"):
        folder = install_root / name
        if folder.is_symlink():
            raise EngineEnrollmentError("Linked Holding state directory")
        if folder.exists():
            if not folder.is_dir() or folder.resolve(strict=True) != folder:
                raise EngineEnrollmentError("Unsafe Holding state directory")
            validate_runtime_acl(folder)
        else:
            folder.mkdir()
            secure_runtime_acl(folder)
    selected = enroll_local_model(
        install_root, selected_model=model_name, approved=True)
    request = EngineEnrollmentRequest(
        install_root=install_root,
        runtime_directory=runtime,
        viewer_sha256=runtime_pins["AtlasHoldingControlCenter.exe"],
        engine_sha256=runtime_pins["AtlasHoldingEngine.exe"],
        repository="r9327/Dof-Atlas",
        canonical_root=atlas_root,
        workspace=install_root / "workspaces",
        health_directory=install_root / "health",
        agent_executable=agent,
        agent_sha256=adapter_sha256,
        agent_arguments=(selected.path,),
        agent_argument_pins={str(selected.path): selected.sha256},
    )
    receipt = enroll_pinned_engine(request, approved=True)
    if not launch_approved:
        return receipt.status, None
    child = launch_enrolled_engine(request, receipt, approved=True)
    return child.status, child.process.pid


def _local_default() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Atlas Holding"
    return Path.home() / "Atlas Holding"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-holding-windows-setup")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--install-root", type=Path, default=_local_default())
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--models-root", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--runtime-viewer-sha256")
    parser.add_argument("--runtime-engine-sha256")
    # Only the trusted installer builder may pin a bundled, protocol-compatible
    # AtlasHoldingAgent.exe; OpenCode/Ollama must not be passed as the adapter.
    parser.add_argument("--adapter-sha256")
    parser.add_argument("--adapter-root", type=Path)
    args = parser.parse_args(argv)
    if (args.adapter_root is None) != (args.adapter_sha256 is None):
        parser.error("Both bundled adapter path and its release pin are required")
    if args.adapter_root is not None and args.runtime_root is None:
        parser.error("Adapter cannot be installed without the engine bundle")
    if args.adapter_root is not None:
        verify_pinned_adapter(
            args.adapter_root / "AtlasHoldingAgent.exe", args.adapter_sha256,
        )
    runtime_fields = (
        args.runtime_root, args.runtime_viewer_sha256, args.runtime_engine_sha256,
    )
    if any(x is not None for x in runtime_fields) and not all(
            x is not None for x in runtime_fields):
        parser.error("Trusted runtime root and both pinned SHA-256 hashes required")
    bundled_runtime_pins = None
    if args.runtime_root is not None:
        bundled_runtime_pins = {
            "AtlasHoldingControlCenter.exe": args.runtime_viewer_sha256,
            "AtlasHoldingEngine.exe": args.runtime_engine_sha256,
        }
        verify_runtime_payload(args.runtime_root, bundled_runtime_pins)

    # Do not import Tk/initialize a display during CLI parsing and unit tests.
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Atlas Holding — Installation Windows")
    root.geometry("820x660")
    root.minsize(700, 540)
    current_install = tk.StringVar(value=str(args.install_root))
    current_cache = tk.StringVar(
        value=str(args.cache_root or (args.install_root / "cache"))
    )
    stored_models = read_model_directory(args.install_root)
    initial_models = (
        args.models_root or stored_models.directory or (args.install_root / "models")
    )
    current_models = tk.StringVar(value=str(initial_models))
    current_atlas = tk.StringVar(value="")
    current_model = tk.StringVar(value="")
    state_message = tk.StringVar(value="Analyse en attente")
    network_message = tk.StringVar(value="Vérification réseau au démarrage")
    file_progress = tk.StringVar(value="Aucun transfert")
    events: Queue[tuple] = Queue()
    abort = threading.Event()
    busy = False
    last_summary: SetupSummary | None = None
    last_pins: tuple[PinnedZip | WingetPin, ...] = ()
    last_paths: tuple[Path, Path, Path] | None = None
    runtime_staged = False

    outer = ttk.Frame(root, padding=16)
    outer.pack(fill=tk.BOTH, expand=True)
    ttk.Label(outer, text="Installer Atlas Holding",
              font=("Segoe UI", 16, "bold")).pack(anchor="w")
    ttk.Label(
        outer,
        text="Atlas réutilise les composants sains et ne télécharge que les "
             "paquets nécessaires, après votre accord.",
        wraplength=700,
    ).pack(anchor="w", pady=(4, 16))

    paths = ttk.LabelFrame(outer, text="Emplacements", padding=10)
    paths.pack(fill=tk.X)
    for label, variable in (("Application et composants", current_install),
                            ("Cache des téléchargements", current_cache),
                            ("Modèles IA (disque)", current_models)):
        frame = ttk.Frame(paths)
        frame.pack(fill=tk.X, pady=3)
        ttk.Label(frame, text=label, width=25).pack(side=tk.LEFT)
        ttk.Entry(frame, textvariable=variable).pack(side=tk.LEFT, fill=tk.X, expand=True)
        def pick(var=variable):
            chosen = filedialog.askdirectory(title="Choisir un dossier")
            if chosen:
                var.set(chosen)
        ttk.Button(frame, text="Parcourir", command=pick).pack(side=tk.LEFT, padx=(8, 0))

    atlas_row = ttk.Frame(paths)
    atlas_row.pack(fill=tk.X, pady=3)
    ttk.Label(atlas_row, text="Clone Git Dof-Atlas", width=25).pack(side=tk.LEFT)
    ttk.Entry(atlas_row, textvariable=current_atlas).pack(
        side=tk.LEFT, fill=tk.X, expand=True)
    def select_atlas() -> None:
        chosen = filedialog.askdirectory(title="Choisir le clone Git r9327/Dof-Atlas")
        if chosen:
            current_atlas.set(chosen)
    ttk.Button(atlas_row, text="Parcourir", command=select_atlas).pack(
        side=tk.LEFT, padx=(8, 0))

    model_row = ttk.Frame(paths)
    model_row.pack(fill=tk.X, pady=3)
    ttk.Label(model_row, text="Modèle Ollama local", width=25).pack(side=tk.LEFT)
    model_choice = ttk.Combobox(
        model_row, textvariable=current_model, state="readonly")
    model_choice.pack(side=tk.LEFT, fill=tk.X, expand=True)
    model_choice["values"] = ()

    ttk.Label(outer, textvariable=network_message).pack(anchor="w", pady=(12, 2))
    ttk.Label(outer, textvariable=state_message).pack(anchor="w", pady=(2, 8))
    output = tk.Text(outer, height=12, wrap=tk.WORD, state=tk.DISABLED)
    output.pack(fill=tk.BOTH, expand=True)
    progress_bar = ttk.Progressbar(outer, maximum=100, mode="determinate")
    progress_bar.pack(fill=tk.X, pady=(12, 2))
    ttk.Label(outer, textvariable=file_progress).pack(anchor="w")

    bottom = ttk.Frame(outer)
    bottom.pack(fill=tk.X, pady=(12, 0))
    check = ttk.Button(bottom, text="Analyser mon PC")
    approve = ttk.Button(bottom, text="Installer les composants nécessaires")
    cancel = ttk.Button(bottom, text="Annuler le transfert")
    activate = ttk.Button(bottom, text="Configurer / lancer le moteur")
    check.pack(side=tk.LEFT)
    approve.pack(side=tk.LEFT, padx=8)
    activate.pack(side=tk.LEFT)
    cancel.pack(side=tk.RIGHT)
    approve.state(["disabled"])
    activate.state(["disabled"])
    cancel.state(["disabled"])

    def write(lines: str) -> None:
        output.configure(state=tk.NORMAL)
        output.delete("1.0", tk.END)
        output.insert("1.0", lines)
        output.configure(state=tk.DISABLED)

    def selected_paths() -> tuple[Path, Path, Path]:
        return (Path(current_install.get()).expanduser(),
                Path(current_cache.get()).expanduser(),
                Path(current_models.get()).expanduser())

    def start(install: bool) -> None:
        nonlocal busy, runtime_staged
        if busy:
            return
        busy = True
        runtime_staged = False
        activate.state(["disabled"])
        abort.clear()
        check.state(["disabled"])
        approve.state(["disabled"])
        cancel.state(["!disabled"] if install else ["disabled"])
        state_message.set("Installation en cours" if install else "Inventaire en cours")
        current_target, current_storage, model_directory = selected_paths()

        def worker() -> None:
            try:
                online = detect_online()
                events.put(("network", online))
                if install:
                    summary, executed = execute_setup(
                        manifest=args.manifest,
                        manifest_sha256=args.manifest_sha256,
                        install_root=current_target,
                        cache_root=current_storage,
                        online=online, approved=True,
                        model_root=model_directory,
                        cancelled=abort.is_set,
                        progress=lambda done, size: events.put(("progress", done, size)),
                        runtime_root=args.runtime_root,
                        runtime_pins=bundled_runtime_pins,
                        adapter_source=(
                            args.adapter_root / "AtlasHoldingAgent.exe"
                            if args.adapter_root is not None else None
                        ),
                        adapter_sha256=args.adapter_sha256,
                    )
                else:
                    summary, pins = preflight_setup(
                        manifest=args.manifest,
                        manifest_sha256=args.manifest_sha256,
                        install_root=current_target,
                        cache_root=current_storage,
                        online=online, model_root=model_directory,
                    )
                    executed = None
                    events.put(("pins", pins))
                events.put(("result", summary, executed,
                            (current_target, current_storage,
                             model_directory)))
                try:
                    models = discover_local_models()
                except LocalModelEnrollmentError:
                    models = ()
                events.put(("models", models))
            except (OSError, ValueError, TypeError, EngineEnrollmentError) as exc:
                # Never dump exception text: a URL or credential may occur in it.
                events.put(("error", type(exc).__name__))

        threading.Thread(target=worker, daemon=True).start()

    def drain() -> None:
        nonlocal busy, last_summary, last_pins, last_paths, runtime_staged
        try:
            while True:
                event = events.get_nowait()
                if event[0] == "network":
                    network_message.set(
                        "Réseau : connecté" if event[1] else
                        "Réseau : hors ligne ou indisponible"
                    )
                elif event[0] == "progress":
                    count, size = event[1:]
                    progress_bar.configure(value=100 * count / size if size else 0)
                    file_progress.set(f"Transfert vérifié : {count:,} / {size:,} octets")
                elif event[0] == "pins":
                    last_pins = event[1]
                elif event[0] == "models":
                    names = tuple(name for name, _digest in event[1])
                    model_choice["values"] = names
                    if current_model.get() not in names:
                        current_model.set(names[0] if len(names) == 1 else "")
                elif event[0] == "result":
                    busy = False
                    last_summary = event[1]
                    executed = event[2]
                    last_paths = event[3]
                    runtime_staged = bool(
                        executed is not None and
                        executed.state == "RUNTIME_STAGED_ENGINE_CONFIG_PENDING"
                    )
                    if (runtime_staged and args.adapter_sha256 is not None and
                            executed is not None and any(
                                step.name == "holding-adapter"
                                and step.result in {
                                    "STAGED_PINNED_ADAPTER_HEALTH_PENDING",
                                    "REUSED_PINNED_ADAPTER_HEALTH_PENDING",
                                } for step in executed.steps)):
                        activate.state(["!disabled"])
                    check.state(["!disabled"])
                    cancel.state(["disabled"])
                    lines = [
                        f"Plateforme : {last_summary.os_name} {last_summary.architecture}",
                        f"GPU : {last_summary.gpu or 'Non détecté'}",
                        f"Modèles locaux trouvés : {last_summary.installed_models}",
                        f"Dossier choisi pour les futurs modèles : {last_paths[2]}",
                        f"Disque des modèles : {last_summary.model_volume_status}",
                        (f"Espace libre modèles : {last_summary.model_volume_free_bytes / 1024**3:.1f} Gio"
                         if last_summary.model_volume_free_bytes is not None
                         else "Espace libre modèles : inconnu"),
                        f"Archives ZIP à prévoir (maximum) : "
                        f"{last_summary.download_upper_bound_bytes / 1024**2:.1f} Mio",
                        (
                            f"Autres téléchargements par winget (taille non estimée) : "
                            f"{last_summary.unestimated_package_downloads}"
                        ),
                        "",
                    ]
                    lines += [
                        f"{name}: {action} — {detail}"
                        for name, action, detail in last_summary.dependency_actions
                    ]
                    if executed is not None:
                        lines += ["", "Résultat : " + executed.state]
                        lines += [f"{s.name}: {s.result}" for s in executed.steps]
                        lines += list(executed.blockers)
                        if (runtime_staged and args.adapter_sha256 is None):
                            lines.append(
                                "Adaptateur IA compatible non embarqué : "
                                "configuration du moteur indisponible."
                            )
                        state_message.set(
                            "Paquets préparés — contrôles logiciels et IA encore nécessaires"
                            if executed.state == "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING"
                            else ("Moteur et interface copiés — configuration et ACL encore nécessaires"
                                  if executed.state == "RUNTIME_STAGED_ENGINE_CONFIG_PENDING"
                                  else "Installation partielle ou bloquée")
                        )
                    else:
                        lines += list(last_summary.blockers)
                        state_message.set(
                            "Prêt à préparer les composants (validation IA ultérieure)"
                            if last_summary.status == "CONSENT_REQUIRED"
                            else last_summary.status
                        )
                        if (last_summary.status == "CONSENT_REQUIRED"
                                or (last_summary.status == "BENCHMARK_REQUIRED"
                                    and args.runtime_root is not None)):
                            # Even when Git/Ollama are already healthy, copying
                            # the two bundled executables requires consent.
                            approve.state(["!disabled"])
                    write("\n".join(lines))
                    if (executed is not None
                            and executed.state in {
                                "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING",
                                "RUNTIME_STAGED_ENGINE_CONFIG_PENDING",
                            }
                            and any(step.name == "ollama"
                                    and step.action == "START_LOCAL_SERVER"
                                    and step.result == "OWNED_API_HEALTHY"
                                    for step in executed.steps)):
                        # Starting at user logon is a separate persistent
                        # change, not covered by initial one-time staging.
                        if messagebox.askyesno(
                            "Relancer Ollama avec Windows",
                            "Après connexion à Windows, relancer Ollama en "
                            "local avec le dossier de modèles choisi ? "
                            "Cela ajoute uniquement une entrée de démarrage "
                            "pour votre compte. Vous pourrez la désactiver.",
                        ):
                            approved_root = last_paths[0]
                            def enroll_worker() -> None:
                                try:
                                    outcome = configure_logon_resume(
                                        approved_root, authorized=True,
                                    )
                                except (OSError, ValueError, TypeError):
                                    outcome = "LOGON_REGISTRATION_FAILED"
                                events.put(("logon_enrolled", outcome))
                            threading.Thread(
                                target=enroll_worker, daemon=True,
                            ).start()
                elif event[0] == "engine_activation":
                    busy = False
                    check.state(["!disabled"])
                    cancel.state(["disabled"])
                    status, pid = event[1:]
                    state_message.set(
                        "Moteur lancé — santé et autonomie non certifiées"
                        if pid is not None else
                        "Moteur configuré — test de démarrage encore nécessaire")
                    output.configure(state=tk.NORMAL)
                    output.insert(tk.END, "\nMoteur Holding : " + status)
                    output.configure(state=tk.DISABLED)
                    # "Configure only" must not strand the wizard; users
                    # may explicitly approve starting the same pinned engine
                    # in a second operation.
                    activate.state(["!disabled"] if pid is None else ["disabled"])
                elif event[0] == "engine_activation_failed":
                    busy = False
                    check.state(["!disabled"])
                    cancel.state(["disabled"])
                    activate.state(["!disabled"])
                    state_message.set("Enrôlement bloqué : " + event[1])
                elif event[0] == "logon_enrolled":
                    status = event[1]
                    output.configure(state=tk.NORMAL)
                    output.insert(tk.END, "\nDémarrage Windows : " + status)
                    output.configure(state=tk.DISABLED)
                    if status not in {"LOGON_RESUME_ENROLLED",
                                      "ALREADY_ENROLLED"}:
                        state_message.set(
                            "Service lancé ; reprise Windows non configurée"
                        )
                elif event[0] == "error":
                    busy = False
                    check.state(["!disabled"])
                    cancel.state(["disabled"])
                    state_message.set("Préparation bloquée : " + event[1])
                    write("Aucun succès d'installation n'est revendiqué. "
                          "Vérifiez les chemins, l'authenticité du manifeste et le disque.")
        except Empty:
            pass
        if root.winfo_exists():
            root.after(150, drain)

    def approve_install() -> None:
        if (last_summary is None
                or last_summary.status not in {"CONSENT_REQUIRED", "BENCHMARK_REQUIRED"}
                or (last_summary.status == "BENCHMARK_REQUIRED"
                    and args.runtime_root is None)):
            return
        if last_paths != selected_paths():
            state_message.set("Emplacements modifiés : nouvelle analyse nécessaire")
            start(False)
            return
        if not messagebox.askyesno(
            "Autoriser l'installation",
            "Autoriser l'installation des paquets vérifiés ? Si Ollama "
            "doit être installé, cette autorisation permet aussi de créer "
            "la variable utilisateur OLLAMA_MODELS pour le disque choisi, "
            "sans écraser une valeur existante différente. Avec un adaptateur "
            "IA embarqué, les droits du dossier d'installation choisi pourront "
            "être restreints à votre compte, SYSTEM et Administrateurs. "
            "N'utilisez pas un dossier contenant des fichiers partagés. "
            "Aucun modèle IA ne sera déclaré prêt sans tests réels.",
        ):
            return
        start(True)

    def activate_engine() -> None:
        nonlocal busy
        if (busy or not runtime_staged or not bundled_runtime_pins
                or args.adapter_sha256 is None or last_paths is None):
            return
        if selected_paths() != last_paths:
            state_message.set("Emplacements modifiés : nouvelle analyse nécessaire")
            return
        chosen = current_atlas.get().strip()
        chosen_model = current_model.get().strip()
        if not chosen_model:
            state_message.set("Choisir un modèle Ollama installé et vérifié")
            return
        if not chosen:
            state_message.set("Choisir le clone Git de Dof-Atlas")
            return
        if not messagebox.askyesno(
            "Configuration du moteur",
            "Configurer le moteur local avec l'adaptateur fourni et vérifié ? "
            "Les permissions du dossier d'installation sélectionné seront "
            "restreintes à votre compte, SYSTEM et Administrateurs. "
            "N'utilisez pas un dossier contenant des fichiers partagés. "
            "Le modèle local sélectionné est lié à son empreinte SHA. "
            "Le dépôt Atlas doit être un vrai clone Git. Aucune fusion GitHub.",
        ):
            return
        run_it = messagebox.askyesno(
            "Démarrer le moteur",
            "Lancer le moteur vérifié après son inscription ? "
            "L'autonomie IA et la santé du moteur restent à confirmer.",
        )
        busy = True
        activate.state(["disabled"])
        check.state(["disabled"])
        def worker() -> None:
            try:
                status, pid = configure_and_launch_engine(
                    install_root=last_paths[0], atlas_root=Path(chosen),
                    runtime_pins=bundled_runtime_pins,
                    adapter_sha256=args.adapter_sha256,
                    approved=True, launch_approved=run_it,
                    model_name=chosen_model,
                )
                events.put(("engine_activation", status, pid))
            except (OSError, ValueError, TypeError,
                    RuntimePayloadError, EngineEnrollmentError) as exc:
                events.put(("engine_activation_failed", type(exc).__name__))
        threading.Thread(target=worker, daemon=True).start()

    check.configure(command=lambda: start(False))
    activate.configure(command=activate_engine)
    approve.configure(command=approve_install)
    cancel.configure(command=abort.set)
    root.after(150, drain)
    root.after(200, lambda: start(False))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

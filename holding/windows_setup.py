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
from .model_volume import inspect_model_volume
from .winget_binary import discover_winget_binary


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
    snapshot = scan(install_root)
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
            # Same exact, vendor-aware pin used by the verified ZIP probe.
            if not matches_component_version(
                    name, spec.version, component.version):
                steps.append(ProvisionStep(name, "POST_INSTALL_HEALTH", "VERSION_MISMATCH"))
                blockers.append("Post-install version mismatch: " + name)
                continue
        steps.append(ProvisionStep(name, "POST_INSTALL_HEALTH", "FOUND_VALID"))
    return ProvisionExecution(
        "POST_INSTALL_RECHECK_REQUIRED" if blockers else
        "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING",
        tuple(steps), tuple(blockers),
    )


def execute_setup(
    *, manifest: Path, manifest_sha256: str, install_root: Path,
    cache_root: Path, online: bool, approved: bool,
    model_root: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[SetupSummary, ProvisionExecution | None]:
    """Recheck every premise before any authorized staging operation."""
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
    snapshot = scan(install_root)
    result = stage_required_dependencies(
        snapshot, pins, online=online, authorized=True,
        install_root=install_root, cache_root=cache_root,
        cancelled=cancelled, progress=progress,
    )
    if result.state == "PAYLOADS_STAGED_NOT_VALIDATED":
        observed = scan(install_root)
        if model_root is not None:
            result, observed = ensure_selected_model_volume(
                result, observed, install_root=install_root,
                model_root=model_root, pins=pins, cancelled=cancelled,
            )
        result = assess_post_install(
            result, observed, pins, install_root=install_root,
        )
    return report, result


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
    args = parser.parse_args(argv)

    # Do not import Tk/initialize a display during CLI parsing and unit tests.
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Atlas Holding — Installation Windows")
    root.geometry("780x570")
    root.minsize(670, 480)
    current_install = tk.StringVar(value=str(args.install_root))
    current_cache = tk.StringVar(
        value=str(args.cache_root or (args.install_root / "cache"))
    )
    stored_models = read_model_directory(args.install_root)
    initial_models = (
        args.models_root or stored_models.directory or (args.install_root / "models")
    )
    current_models = tk.StringVar(value=str(initial_models))
    state_message = tk.StringVar(value="Analyse en attente")
    network_message = tk.StringVar(value="Vérification réseau au démarrage")
    file_progress = tk.StringVar(value="Aucun transfert")
    events: Queue[tuple] = Queue()
    abort = threading.Event()
    busy = False
    last_summary: SetupSummary | None = None
    last_pins: tuple[PinnedZip | WingetPin, ...] = ()
    last_paths: tuple[Path, Path, Path] | None = None

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
    check.pack(side=tk.LEFT)
    approve.pack(side=tk.LEFT, padx=8)
    cancel.pack(side=tk.RIGHT)
    approve.state(["disabled"])
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
        nonlocal busy
        if busy:
            return
        busy = True
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
            except (OSError, ValueError, TypeError) as exc:
                # Never dump exception text: a URL or credential may occur in it.
                events.put(("error", type(exc).__name__))

        threading.Thread(target=worker, daemon=True).start()

    def drain() -> None:
        nonlocal busy, last_summary, last_pins, last_paths
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
                elif event[0] == "result":
                    busy = False
                    last_summary = event[1]
                    executed = event[2]
                    last_paths = event[3]
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
                        state_message.set(
                            "Paquets préparés — contrôles logiciels et IA encore nécessaires"
                            if executed.state == "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING"
                            else "Installation partielle ou bloquée"
                        )
                    else:
                        lines += list(last_summary.blockers)
                        state_message.set(
                            "Prêt à préparer les composants (validation IA ultérieure)"
                            if last_summary.status == "CONSENT_REQUIRED"
                            else last_summary.status
                        )
                        if last_summary.status == "CONSENT_REQUIRED":
                            approve.state(["!disabled"])
                    write("\n".join(lines))
                    if (executed is not None
                            and executed.state ==
                            "TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING"
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
        if last_summary is None or last_summary.status != "CONSENT_REQUIRED":
            return
        if last_paths != selected_paths():
            state_message.set("Emplacements modifiés : nouvelle analyse nécessaire")
            start(False)
            return
        if not messagebox.askyesno(
            "Autoriser l'installation",
            "Autoriser une seule préparation des paquets vérifiés pour "
            "ce PC ? Aucun logiciel ni modèle IA ne sera déclaré prêt "
            "sans tests réels.",
        ):
            return
        start(True)

    check.configure(command=lambda: start(False))
    approve.configure(command=approve_install)
    cancel.configure(command=abort.set)
    root.after(150, drain)
    root.after(200, lambda: start(False))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

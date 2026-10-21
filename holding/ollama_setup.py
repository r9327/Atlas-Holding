"""Connect the Windows bootstrap step to Ollama service and model volume.

Neither a pre-existing daemon nor a successful process spawn attests model
quality; the full toolchain is rescanned after a new service starts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .inventory import Inventory, scan
from .ollama_service import start_local_ollama
from .ollama_storage import store_model_directory
from .post_ollama_inventory import scan_ollama_with_pins
from .provision_runtime import ProvisionExecution, ProvisionStep
from .staged_install import PinnedZip
from .windows_package_manager import WingetPin


def ensure_selected_model_volume(
    execution: ProvisionExecution, inventory: Inventory, *,
    install_root: Path, model_root: Path,
    pins: tuple[PinnedZip | WingetPin, ...],
    cancelled: Callable[[], bool] | None = None,
) -> tuple[ProvisionExecution, Inventory]:
    """Keep an existing server untouched; start an absent server if possible."""
    if not model_root.is_absolute():
        raise ValueError("Absolute models storage path required")
    if inventory.ollama.status == "FOUND_VALID":
        step = ProvisionStep(
            "ollama", "MODEL_STORAGE",
            "EXISTING_SERVER_STORAGE_NOT_ATTESTED",
        )
        return ProvisionExecution(
            execution.state, execution.steps + (step,), execution.blockers,
        ), inventory
    session = start_local_ollama(
        inventory, install_root=install_root, model_root=model_root,
        pins=pins,
        authorized=True, cancelled=cancelled,
    )
    step = ProvisionStep("ollama", "START_LOCAL_SERVER", session.state)
    if session.state != "OWNED_API_HEALTHY":
        return ProvisionExecution(
            "PARTIAL_OR_BLOCKED", execution.steps + (step,),
            ("Local Ollama startup failed or requires inspection",),
        ), inventory
    saved = store_model_directory(
        install_root, model_root, authorized=True,
    )
    stored = ProvisionStep("ollama", "MODEL_STORAGE", saved.status)
    if saved.status != "STORED_PREFERENCE_NOT_RUNTIME_ATTESTED":
        return ProvisionExecution(
            "PARTIAL_OR_BLOCKED",
            execution.steps + (step, stored),
            ("Ollama started but its storage selection could not be retained",),
        ), inventory
    return ProvisionExecution(
        execution.state, execution.steps + (step, stored), execution.blockers,
    ), scan_ollama_with_pins(install_root, pins)

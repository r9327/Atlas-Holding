"""Strict validation for the declarative, non-executable tool catalogue."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


MAX_REGISTRY_BYTES = 512 * 1024
_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_NETWORK_GUARDS = frozenset({
    "quota_and_network_guard", "approved_domains_and_budget_only",
    "ci_on_final_ready_only",
})
_LOCAL_AUTOMATION = frozenset({
    "if_exact_sha_verified", "via_project_contract", "controlled_executor_only",
    "if_environment_supported",
})
_MUTATION_RISKS = frozenset({"repo_mutation_explicit", "external_mutation_explicit"})


class ToolRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ToolPolicy:
    tool_id: str
    capabilities: tuple[str, ...]
    network: bool
    required: bool
    risk: str
    automatic: bool | str


@dataclass(frozen=True)
class ToolRegistry:
    tools: tuple[ToolPolicy, ...]
    capability_owner: tuple[tuple[str, str], ...]
    paid_services_allowed: bool = False
    executable_hub_ready: bool = False


def _exact(value: Any, required: set[str], optional: set[str], label: str) -> dict[str, Any]:
    if (not isinstance(value, dict) or not required.issubset(value)
            or not set(value).issubset(required | optional)):
        raise ToolRegistryError(f"Invalid {label} shape")
    return value


def validate_tool_registry(raw: bytes) -> ToolRegistry:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_REGISTRY_BYTES:
        raise ToolRegistryError("Invalid bounded registry payload")
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ToolRegistryError("Tool registry is not valid JSON") from exc
    data = _exact(payload, {
        "schema_version", "allow_paid_services", "default_network",
        "max_concurrent_heavy_models", "tools", "plugin_selection_policy",
        "local_mcp_hub",
    }, set(), "registry")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ToolRegistryError("Unsupported registry schema")
    if data["allow_paid_services"] is not False or data["default_network"] != "offline":
        raise ToolRegistryError("Registry must remain zero-cost and offline by default")
    if (type(data["max_concurrent_heavy_models"]) is not int
            or data["max_concurrent_heavy_models"] != 1):
        raise ToolRegistryError("Heavy model concurrency must remain one")
    if data["plugin_selection_policy"] != (
            "capability_local_verified_first_then_cached_then_authorized_remote_then_native_fallback"):
        raise ToolRegistryError("Unsupported plugin selection policy")

    hub = _exact(data["local_mcp_hub"], {
        "implemented", "transport", "network_default", "discovery", "permissions",
    }, set(), "local MCP hub")
    if (hub["implemented"] is not False or hub["transport"] != "stdio"
            or hub["network_default"] != "offline" or hub["discovery"] != "per_task"
            or hub["permissions"] != "default_read_only"):
        raise ToolRegistryError("Unproved or unsafe local MCP hub declaration")

    if not isinstance(data["tools"], list) or not 1 <= len(data["tools"]) <= 100:
        raise ToolRegistryError("Invalid bounded tool list")
    policies = []
    owners: dict[str, str] = {}
    ids = set()
    for value in data["tools"]:
        item = _exact(value, {
            "id", "capabilities", "transport", "network", "risk", "automatic",
        }, {
            "required", "required_for", "prefer_local", "offline_mode",
            "installed_locally", "self_host_optional", "local_resource_budget_required",
        }, "tool")
        tool_id = item["id"]
        if not isinstance(tool_id, str) or not _ID.fullmatch(tool_id) or tool_id in ids:
            raise ToolRegistryError("Invalid or duplicate tool id")
        ids.add(tool_id)
        capabilities = item["capabilities"]
        if (not isinstance(capabilities, list) or not capabilities
                or len(capabilities) > 30 or len(set(capabilities)) != len(capabilities)
                or any(not isinstance(cap, str) or not _CAPABILITY.fullmatch(cap)
                       for cap in capabilities)):
            raise ToolRegistryError("Invalid tool capabilities")
        for capability in capabilities:
            if capability in owners:
                raise ToolRegistryError("Capability has multiple declared owners")
            owners[capability] = tool_id
        if type(item["network"]) is not bool:
            raise ToolRegistryError("Invalid network declaration")
        if (not isinstance(item["transport"], str) or not item["transport"]
                or len(item["transport"]) > 100):
            raise ToolRegistryError("Invalid transport declaration")
        required = item.get("required", False)
        if type(required) is not bool:
            raise ToolRegistryError("Invalid required declaration")
        if item["network"] and required:
            raise ToolRegistryError("A network tool cannot be mandatory offline")
        automatic = item["automatic"]
        if not isinstance(automatic, (bool, str)) or isinstance(automatic, str) and not automatic:
            raise ToolRegistryError("Invalid automation policy")
        if item["network"] and automatic not in _NETWORK_GUARDS:
            raise ToolRegistryError("Network automation lacks an explicit guard")
        if not item["network"] and isinstance(automatic, str) and automatic not in _LOCAL_AUTOMATION:
            raise ToolRegistryError("Unknown local automation guard")
        risk = item["risk"]
        if not isinstance(risk, str) or not risk:
            raise ToolRegistryError("Invalid risk declaration")
        if risk in _MUTATION_RISKS and automatic is True:
            raise ToolRegistryError("Mutation cannot be unconditionally automatic")
        for field in ("prefer_local", "installed_locally", "self_host_optional",
                      "local_resource_budget_required"):
            if field in item and type(item[field]) is not bool:
                raise ToolRegistryError(f"Invalid {field} declaration")
        if item.get("installed_locally") is True:
            raise ToolRegistryError("Registry cannot self-certify a local installation")
        if "offline_mode" in item and (not item["network"]
                or not isinstance(item["offline_mode"], str) or not item["offline_mode"]):
            raise ToolRegistryError("Invalid offline mode declaration")
        if "required_for" in item:
            scopes = item["required_for"]
            if (not isinstance(scopes, list) or not scopes or len(set(scopes)) != len(scopes)
                    or any(not isinstance(scope, str) or not scope for scope in scopes)):
                raise ToolRegistryError("Invalid conditional requirement")
        policies.append(ToolPolicy(
            tool_id, tuple(capabilities), item["network"], required, risk, automatic,
        ))
    return ToolRegistry(tuple(policies), tuple(sorted(owners.items())))

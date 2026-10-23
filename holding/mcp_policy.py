"""Executor-side MCP capability, permission, quota and offline policy.

This module authorizes a call; it does not execute plugins or trust text
returned by them. Registry entries and grants must come from the trusted local
control plane, never from an agent response or scraped page.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from urllib.parse import urlsplit


_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PERMISSIONS = frozenset({"READ", "CACHE_WRITE"})


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class PluginSpec:
    plugin_id: str
    version: str
    capabilities: frozenset[str]
    permissions: frozenset[str] = frozenset({"READ"})
    requires_network: bool = False
    requires_auth: bool = False
    request_limit: int = 0
    credit_limit: int = 0
    max_response_bytes: int = 1024 * 1024
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if (type(self.plugin_id) is not str or not _ID.fullmatch(self.plugin_id)
                or type(self.version) is not str or not _VERSION.fullmatch(self.version)
                or type(self.capabilities) is not frozenset or not self.capabilities
                or any(type(item) is not str or not _ID.fullmatch(item)
                       for item in self.capabilities)
                or type(self.permissions) is not frozenset
                or not self.permissions <= _PERMISSIONS
                or "READ" not in self.permissions
                or type(self.requires_network) is not bool
                or type(self.requires_auth) is not bool
                or type(self.request_limit) is not int or self.request_limit < 0
                or type(self.credit_limit) is not int or self.credit_limit < 0
                or type(self.max_response_bytes) is not int
                or not 1 <= self.max_response_bytes <= 2 * 1024 * 1024
                or type(self.timeout_seconds) is not int
                or not 1 <= self.timeout_seconds <= 60):
            raise PolicyError("Invalid bounded plugin registry entry")
        if self.requires_auth and not self.requires_network:
            raise PolicyError("Local-only plugin cannot require remote authentication")


@dataclass(frozen=True)
class PermissionGrant:
    plugin_id: str
    permissions: frozenset[str]
    expires_epoch: int

    def __post_init__(self) -> None:
        if (type(self.plugin_id) is not str or not _ID.fullmatch(self.plugin_id)
                or type(self.permissions) is not frozenset
                or not self.permissions <= _PERMISSIONS
                or "READ" in self.permissions
                or type(self.expires_epoch) is not int or self.expires_epoch < 1):
            raise PolicyError("Invalid plugin permission grant")


@dataclass(frozen=True)
class CacheEvidence:
    source_uri: str
    captured_epoch: int
    content_sha256: str
    source_version: str

    def valid_at(self, now_epoch: int, max_age_seconds: int) -> bool:
        parts = urlsplit(self.source_uri) if type(self.source_uri) is str else None
        return bool(
            parts and parts.scheme == "https" and parts.hostname
            and parts.username is None and parts.password is None
            and type(self.captured_epoch) is int
            and 0 <= self.captured_epoch <= now_epoch
            and now_epoch - self.captured_epoch <= max_age_seconds
            and type(self.content_sha256) is str
            and _SHA256.fullmatch(self.content_sha256)
            and type(self.source_version) is str and self.source_version
        )


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str


class PluginGate:
    """Small in-memory claim gate; durable accounting belongs to Holding Core."""

    def __init__(self, specs: tuple[PluginSpec, ...],
                 grants: tuple[PermissionGrant, ...] = ()) -> None:
        if (type(specs) is not tuple or type(grants) is not tuple
                or any(type(item) is not PluginSpec for item in specs)
                or any(type(item) is not PermissionGrant for item in grants)):
            raise PolicyError("Registry and grants must be validated immutable records")
        if len({item.plugin_id for item in specs}) != len(specs):
            raise PolicyError("Duplicate plugin registry identity")
        self._specs = {item.plugin_id: item for item in specs}
        self._grants = grants
        self._requests: dict[str, int] = {}
        self._credits: dict[str, int] = {}
        self._quota_lock = threading.Lock()

    def claim(self, *, plugin_id: str, capability: str, permission: str,
              now_epoch: int, online: bool, auth_available: bool,
              credit_cost: int = 0, cache: CacheEvidence | None = None,
              max_cache_age_seconds: int = 86400) -> Decision:
        if (type(plugin_id) is not str or type(capability) is not str
                or type(permission) is not str
                or type(now_epoch) is not int or now_epoch < 0
                or type(online) is not bool or type(auth_available) is not bool
                or type(credit_cost) is not int or credit_cost < 0
                or type(max_cache_age_seconds) is not int
                or max_cache_age_seconds < 0):
            return Decision("BLOCKED", "Invalid trusted execution context")
        spec = self._specs.get(plugin_id)
        if spec is None or capability not in spec.capabilities:
            return Decision("BLOCKED", "Unknown plugin or capability")
        if permission not in _PERMISSIONS or permission not in spec.permissions:
            return Decision("BLOCKED", "Operation is outside registry permissions")
        if permission != "READ" and not any(
                grant.plugin_id == plugin_id and permission in grant.permissions
                and grant.expires_epoch > now_epoch for grant in self._grants):
            return Decision("BLOCKED", "No active executor-side permission grant")
        if not spec.requires_network:
            return Decision("READY_LOCAL", "Verified local capability")
        if not online:
            if cache is not None and cache.valid_at(now_epoch, max_cache_age_seconds):
                return Decision("CACHED_ONLY", "Fresh versioned cache; not live data")
            return Decision("OFFLINE_DEFERRED", "Remote capability unavailable offline")
        if spec.requires_auth and not auth_available:
            return Decision("AUTH_REQUIRED", "Remote credential unavailable")
        with self._quota_lock:
            requests = self._requests.get(plugin_id, 0)
            credits = self._credits.get(plugin_id, 0)
            if (requests >= spec.request_limit
                    or credits + credit_cost > spec.credit_limit):
                return Decision("QUOTA_EXHAUSTED", "Service or credit quota exhausted")
            self._requests[plugin_id] = requests + 1
            self._credits[plugin_id] = credits + credit_cost
        return Decision("READY_ONLINE", "Authorized bounded remote capability")

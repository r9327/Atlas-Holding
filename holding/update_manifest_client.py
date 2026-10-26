"""Conditional GitHub release-manifest probe (ETag / 304).

This performs only a bounded HTTPS GET and schema check, never installs a
release or declares an unverified manifest trusted/signed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .update_download import _check_url, _open_url, ArtifactDownloadError
from .update_plan import UpdateManifest, InvalidUpdateManifest

_MAX_MANIFEST_BYTES = 256 * 1024


class ManifestFetchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ManifestCheck:
    status: str  # UPDATED / UNCHANGED
    manifest: UpdateManifest | None
    etag: str | None
    bytes_received: int


def _etag(value: object) -> str | None:
    if (isinstance(value, str) and 2 <= len(value) <= 200
            and ((value.startswith('"') and value.endswith('"')) or
                 (value.startswith('W/"') and value.endswith('"')))
            and all(ord(ch) >= 32 and ord(ch) < 127 for ch in value)):
        return value
    return None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    doc = {}
    for key, value in pairs:
        if key in doc:
            raise ManifestFetchError("duplicate manifest key")
        doc[key] = value
    return doc


def check_manifest(url: str, *, previous_etag: str | None = None,
                   timeout: float = 15.0, allow_test_loopback: bool = False
                   ) -> ManifestCheck:
    try:
        _check_url(url, allow_test_loopback=allow_test_loopback)
    except ArtifactDownloadError as exc:
        raise ManifestFetchError("invalid manifest URL") from exc
    if not isinstance(timeout, (float, int)) or not 0 < timeout <= 120:
        raise ManifestFetchError("invalid timeout")
    if previous_etag is not None and _etag(previous_etag) is None:
        raise ManifestFetchError("invalid cache ETag")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity",
               "User-Agent": "AtlasHoldingUpdater/1"}
    if previous_etag is not None:
        headers["If-None-Match"] = previous_etag
    req = Request(url, headers=headers)
    try:
        with _open_url(req, timeout=float(timeout), allow_test_loopback=allow_test_loopback) as response:
            _check_url(response.geturl(), allow_test_loopback=allow_test_loopback)
            if response.status != 200:
                raise ManifestFetchError("unexpected manifest response")
            body = response.read(_MAX_MANIFEST_BYTES + 1)
            etag = _etag(response.headers.get("ETag"))
    except HTTPError as exc:
        if exc.code == 304 and previous_etag is not None:
            return ManifestCheck("UNCHANGED", None, previous_etag, 0)
        raise ManifestFetchError("manifest HTTP error") from exc
    except (URLError, OSError, ArtifactDownloadError) as exc:
        raise ManifestFetchError("manifest network error") from exc
    if len(body) > _MAX_MANIFEST_BYTES:
        raise ManifestFetchError("manifest exceeds 256 KiB")
    try:
        payload = json.loads(body, object_pairs_hook=_unique_object,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        manifest = UpdateManifest.parse(payload)
    except (UnicodeError, ValueError, TypeError, InvalidUpdateManifest) as exc:
        raise ManifestFetchError("invalid release manifest") from exc
    return ManifestCheck("UPDATED", manifest, etag, len(body))

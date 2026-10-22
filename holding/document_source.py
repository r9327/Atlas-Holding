"""Small, bounded HTTP JSON/CSV source for the Documentalist.

Reads explicitly approved hosts only, never runs page scripts or instructions.
The collection is immutable and requires an explicitly selected format.
A browser/crawler adapter can be added later without replacing this pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler, Request, build_opener,
)

from .document_csv import ingest_csv_records
from .document_intake import (
    DocumentBatch, DocumentIntakeError, MAX_DOCUMENT_BYTES, ingest_json_records,
)


class DocumentSourceError(ValueError):
    pass


def _check_url(url: str, hosts: frozenset[str], *, test_loopback: bool) -> str:
    if not isinstance(url, str) or len(url) > 4096:
        raise DocumentSourceError("invalid URL")
    parsed = urlsplit(url)
    if (not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.scheme not in ("https", "http")):
        raise DocumentSourceError("unsupported URL")
    host = parsed.hostname.lower()
    if host not in hosts:
        raise DocumentSourceError("host is not explicitly approved")
    try:
        numeric = ipaddress.ip_address(host)
    except ValueError:
        numeric = None
    if numeric and (not test_loopback or not numeric.is_loopback):
        raise DocumentSourceError("IP address sources are disabled")
    if host == "localhost" and not test_loopback:
        raise DocumentSourceError("localhost source is disabled")
    if parsed.scheme != "https" and not (
        test_loopback and parsed.scheme == "http"
        and host in {"127.0.0.1", "::1", "localhost"}
    ):
        raise DocumentSourceError("HTTPS required")
    if parsed.port is not None and not (test_loopback or parsed.port == 443):
        raise DocumentSourceError("unexpected port")
    return host


class _ApprovedRedirects(HTTPRedirectHandler):
    def __init__(self, approved: frozenset[str], *, test_loopback: bool):
        super().__init__()
        self.approved = approved
        self.test_loopback = test_loopback

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl, self.approved, test_loopback=self.test_loopback)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True, slots=True)
class WebDocument:
    batch: DocumentBatch
    bytes_received: int
    content_type: str


def fetch_document(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    format: str,
    id_field: str = "id",
    timeout: float = 15.0,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    allow_test_loopback: bool = False,
) -> WebDocument:
    """Fetch one small JSON/CSV document; no JS, no crawl or hidden downloads.

    allowed_hosts is explicit per mission. Links within fetched content are
    always data and cannot expand the list of approved hosts.
    """
    if not isinstance(allowed_hosts, (tuple, list)) or not allowed_hosts:
        raise DocumentSourceError("at least one approved host is required")
    if any(not isinstance(h, str) or not h or h.lower() != h
           or "/" in h or ":" in h and h != "::1" for h in allowed_hosts):
        raise DocumentSourceError("invalid host allowlist")
    hosts = frozenset(allowed_hosts)
    _check_url(url, hosts, test_loopback=allow_test_loopback)
    if format not in ("json", "csv"):
        raise DocumentSourceError("format must be json or csv")
    if (not isinstance(timeout, (int, float)) or not 0 < timeout <= 60
            or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_DOCUMENT_BYTES):
        raise DocumentSourceError("invalid transfer limits")
    request = Request(
        url,
        headers={
            "Accept-Encoding": "identity",
            "Accept": "application/json" if format == "json" else "text/csv",
            "User-Agent": "AtlasHolding-Documentalist/1",
        },
    )
    opener = build_opener(
        _ApprovedRedirects(hosts, test_loopback=allow_test_loopback),
    )
    try:
        with opener.open(request, timeout=float(timeout)) as response:
            final_url = response.geturl()
            _check_url(final_url, hosts, test_loopback=allow_test_loopback)
            if response.status != 200:
                raise DocumentSourceError("unexpected HTTP status")
            content_type = response.headers.get_content_type().lower()
            expected = ("application/json", "text/json") if format == "json" else ("text/csv", "application/csv")
            if content_type not in expected:
                raise DocumentSourceError("unexpected source content type")
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise DocumentSourceError("document exceeds size budget")
                except ValueError as exc:
                    raise DocumentSourceError("invalid Content-Length") from exc
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise DocumentSourceError("document exceeds size budget")
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        raise DocumentSourceError("source fetch failed") from exc
    stamp = datetime.now(timezone.utc).isoformat()
    ingest = ingest_json_records if format == "json" else ingest_csv_records
    try:
        batch = ingest(raw, source=final_url, collected_at=stamp, id_field=id_field)
    except DocumentIntakeError as exc:
        raise DocumentSourceError("source document is invalid") from exc
    return WebDocument(batch, len(raw), content_type)

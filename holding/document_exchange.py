"""Portable, versioned wire format for Documentalist records.

This generic exporter knows nothing about Atlas Quests/Guides/Succès. Consumers
validate project-specific schemas after decoding. No filesystem side effects.
"""
from __future__ import annotations

import json

from .document_intake import DocumentBatch, DocumentIntakeError, MAX_DOCUMENT_BYTES


def export_document_batch(batch: DocumentBatch) -> bytes:
    if not isinstance(batch, DocumentBatch):
        raise DocumentIntakeError("expected an immutable DocumentBatch")
    records = []
    for record in batch.records:
        try:
            payload = json.loads(record.canonical_json)
        except (ValueError, TypeError) as exc:
            raise DocumentIntakeError("invalid normalized record") from exc
        records.append({
            "id": record.record_id,
            "sha256": record.sha256,
            "payload": payload,
        })
    document = {
        "schema": "documentalist-batch/v1",
        "source": batch.source,
        "collected_at": batch.collected_at,
        "source_sha256": batch.source_sha256,
        "records": records,
    }
    raw = json.dumps(document, ensure_ascii=False, allow_nan=False,
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise DocumentIntakeError("exchange batch exceeds 8 MiB")
    return raw

"""Canonical SQLite schema statements shared by startup and migrations.

Keep schema SQL here so a table cannot silently diverge between the daemon,
standalone QA processes, and versioned upgrades.  Compatibility callers may
still apply a subset idempotently until every entry point is migration-aware.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable


CORE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS missions (
        id TEXT PRIMARY KEY,
        repo TEXT NOT NULL,
        operation TEXT NOT NULL,
        dependencies TEXT NOT NULL,
        priority INTEGER NOT NULL,
        state TEXT NOT NULL,
        candidate_sha TEXT NOT NULL DEFAULT '',
        evidence_sha TEXT NOT NULL DEFAULT '',
        external_required INTEGER NOT NULL DEFAULT 0,
        product_decision_required INTEGER NOT NULL DEFAULT 0,
        failures_without_progress INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS grants (
        repo TEXT NOT NULL,
        operation TEXT NOT NULL,
        expires_epoch INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(repo, operation)
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        mission_id TEXT,
        event TEXT NOT NULL,
        detail TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
)

DAEMON_LEASE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS daemon_lease (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        owner TEXT NOT NULL,
        expires_epoch INTEGER NOT NULL
    )""",
)

MODEL_SCHEMA_V1 = (
    """CREATE TABLE IF NOT EXISTS model_profiles (
        digest_sha256 TEXT PRIMARY KEY,
        model_name TEXT NOT NULL,
        provider TEXT NOT NULL,
        quantization TEXT NOT NULL,
        context_measured INTEGER NOT NULL CHECK(context_measured>0),
        ram_peak_bytes INTEGER NOT NULL CHECK(ram_peak_bytes>=0),
        vram_peak_bytes INTEGER NOT NULL CHECK(vram_peak_bytes>=0),
        benchmark_digest TEXT NOT NULL,
        observed_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mission_model_usage (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        role TEXT NOT NULL,
        model_digest_sha256 TEXT NOT NULL REFERENCES model_profiles(digest_sha256),
        candidate_sha TEXT NOT NULL,
        observed_at INTEGER NOT NULL
    )""",
)

GIT_JOURNAL_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS git_operations (
        mission_id TEXT NOT NULL REFERENCES missions(id),
        operation_key TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('CREATE', 'COMMIT')),
        intent_digest TEXT NOT NULL,
        intent_json TEXT NOT NULL,
        base_sha TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('PREPARED', 'APPLIED', 'INSPECT_REQUIRED')),
        result_sha TEXT NOT NULL DEFAULT '',
        updated_epoch INTEGER NOT NULL,
        PRIMARY KEY (mission_id, operation_key)
    )""",
)

SIGNED_PROOF_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS signed_proofs (
        mission_id TEXT PRIMARY KEY REFERENCES missions(id),
        candidate_sha TEXT NOT NULL,
        verdict TEXT NOT NULL,
        artifact TEXT NOT NULL,
        receipt_digest TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS signed_proof_history (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        mission_id TEXT NOT NULL REFERENCES missions(id),
        candidate_sha TEXT NOT NULL,
        verdict TEXT NOT NULL,
        artifact TEXT NOT NULL,
        receipt_digest TEXT NOT NULL,
        recorded_epoch INTEGER NOT NULL,
        UNIQUE(mission_id,candidate_sha)
    )""",
)

SCOPED_QA_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS scoped_qa_advisories (
        mission_id TEXT PRIMARY KEY REFERENCES missions(id),
        candidate_sha TEXT NOT NULL,
        status TEXT NOT NULL,
        base_sha TEXT NOT NULL,
        report_sha256 TEXT NOT NULL,
        checked_epoch INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS scoped_qa_retries (
        mission_id TEXT PRIMARY KEY REFERENCES missions(id),
        candidate_sha TEXT NOT NULL,
        attempts INTEGER NOT NULL,
        next_epoch INTEGER NOT NULL
    )""",
)

QA_REPAIR_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS qa_repair_attempts (
        mission_id TEXT NOT NULL REFERENCES missions(id),
        failed_sha TEXT NOT NULL,
        requeued_epoch INTEGER NOT NULL,
        PRIMARY KEY(mission_id, failed_sha)
    )""",
)

ROADMAP_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS roadmap_versions (
        version INTEGER PRIMARY KEY AUTOINCREMENT,
        repo TEXT NOT NULL,
        digest TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL,
        imported_epoch INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mission_specs (
        id TEXT PRIMARY KEY REFERENCES missions(id),
        spec_json TEXT NOT NULL,
        version INTEGER NOT NULL REFERENCES roadmap_versions(version)
    )""",
    """CREATE TABLE IF NOT EXISTS roadmap_amendments (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        version INTEGER NOT NULL REFERENCES roadmap_versions(version),
        changed_ids_json TEXT NOT NULL,
        recorded_epoch INTEGER NOT NULL
    )""",
)

ROADMAP_AMENDMENT_SCHEMA = (ROADMAP_SCHEMA[2],)
RUNTIME_SCHEMA_V2 = (
    DAEMON_LEASE_SCHEMA
    + GIT_JOURNAL_SCHEMA
    + SIGNED_PROOF_SCHEMA
    + SCOPED_QA_SCHEMA
    + QA_REPAIR_SCHEMA
    + ROADMAP_SCHEMA
)


def apply_schema(db: sqlite3.Connection, statements: Iterable[str]) -> None:
    """Apply canonical statements without the implicit commits of executescript."""
    for statement in statements:
        db.execute(statement)

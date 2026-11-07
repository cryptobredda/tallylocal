"""Forward-only, idempotent schema migrations.

The original app only ever called create_all(), so existing installs would never
gain new columns. This module adds them in place and records a version, without
pulling in Alembic for a single-file SQLite database.
"""
import json
import os
from datetime import datetime

from sqlalchemy import text

CURRENT_VERSION = 2
VIEW_NAME = 'v_transactions_effective'

VIEW_SQL = """
CREATE VIEW IF NOT EXISTS v_transactions_effective AS
SELECT
    t.id                      AS id,
    t.date                    AS date,
    COALESCE(o.description, t.description) AS description,
    t.original_description    AS original_description,
    t.amount                  AS amount,
    t.transaction_type        AS transaction_type,
    t.balance_after           AS balance_after,
    COALESCE(o.category_id, t.category_id) AS category_id,
    t.account_id              AS account_id,
    t.linked_transaction_id   AS linked_transaction_id,
    t.is_internal_transfer    AS is_internal_transfer,
    COALESCE(o.is_one_off, t.is_one_off)   AS is_one_off,
    COALESCE(o.is_recurring, t.is_recurring) AS is_recurring,
    COALESCE(o.notes, t.notes) AS notes,
    COALESCE(o.tags, t.tags)   AS tags,
    t.bank_name               AS bank_name,
    t.account_number          AS account_number,
    t.statement_file          AS statement_file,
    t.source                  AS source,
    t.external_id             AS external_id,
    t.import_hash             AS import_hash,
    t.import_batch_id         AS import_batch_id,
    t.currency                AS currency,
    t.amount_base             AS amount_base,
    t.fx_rate                 AS fx_rate,
    t.pending                 AS pending,
    t.value_date              AS value_date,
    t.counterparty            AS counterparty,
    t.merchant_normalized     AS merchant_normalized,
    t.raw_json                AS raw_json,
    t.created_at              AS created_at,
    t.updated_at              AS updated_at,
    COALESCE(o.is_excluded, 0) AS is_excluded,
    CASE WHEN o.transaction_id IS NULL THEN 0 ELSE 1 END AS is_edited
FROM transactions t
LEFT JOIN transaction_overrides o ON o.transaction_id = t.id
WHERE COALESCE(o.is_excluded, 0) = 0
"""

ACCOUNT_COLUMNS = [
    ('currency', "VARCHAR(3) DEFAULT 'GBP'"),
    ('connection_id', 'INTEGER'),
    ('provider_account_id', 'VARCHAR(160)'),
    ('iban_masked', 'VARCHAR(40)'),
    ('balance_current', 'FLOAT'),
    ('balance_available', 'FLOAT'),
    ('balance_synced_at', 'DATETIME'),
    ('balance_source', "VARCHAR(20) DEFAULT 'opening'"),
]

TRANSACTION_COLUMNS = [
    ('source', "VARCHAR(20) DEFAULT 'manual'"),
    ('external_id', 'VARCHAR(160)'),
    ('import_hash', 'VARCHAR(64)'),
    ('import_batch_id', 'INTEGER'),
    ('currency', 'VARCHAR(3)'),
    ('amount_base', 'FLOAT'),
    ('fx_rate', 'FLOAT'),
    ('pending', 'BOOLEAN DEFAULT 0'),
    ('value_date', 'DATETIME'),
    ('counterparty', 'VARCHAR(200)'),
    ('merchant_normalized', 'VARCHAR(120)'),
    ('raw_json', 'TEXT'),
]

INDEXES = [
    'CREATE UNIQUE INDEX IF NOT EXISTS uq_tx_account_external ON transactions (account_id, external_id)',
    'CREATE UNIQUE INDEX IF NOT EXISTS uq_tx_account_import_hash ON transactions (account_id, import_hash)',
    'CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions (date)',
    'CREATE INDEX IF NOT EXISTS idx_tx_account_date ON transactions (account_id, date)',
    'CREATE INDEX IF NOT EXISTS idx_tx_batch ON transactions (import_batch_id)',
    'CREATE INDEX IF NOT EXISTS idx_tx_source ON transactions (source)',
    'CREATE INDEX IF NOT EXISTS idx_edits_tx ON transaction_edits (transaction_id)',
    'CREATE INDEX IF NOT EXISTS idx_audit_created ON agent_audit (created_at)',
]


def _columns(conn, table):
    try:
        rows = conn.execute(text(f'PRAGMA table_info({table})')).fetchall()
    except Exception:
        return set()
    return {r[1] for r in rows}


def _has_table(conn, table):
    row = conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = :n"
    ), {'n': table}).fetchone()
    return row is not None


def create_views(conn):
    """(Re)create read-only projections. Safe to call before tables exist."""
    conn.execute(text('DROP VIEW IF EXISTS v_transactions_effective'))
    conn.execute(text(VIEW_SQL))


def _add_columns(conn, table, columns):
    if not _has_table(conn, table):
        return []
    existing = _columns(conn, table)
    added = []
    for name, ddl in columns:
        if name not in existing:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
            added.append(name)
    return added


def _version(conn):
    if not _has_table(conn, 'schema_version'):
        conn.execute(text(
            'CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at DATETIME)'
        ))
        return 0
    row = conn.execute(text('SELECT MAX(version) FROM schema_version')).fetchone()
    return row[0] or 0


def ensure_schema(db_path):
    """Bring a database (fresh or legacy) up to CURRENT_VERSION. Idempotent."""
    from app.database import make_engine

    engine = make_engine(db_path)
    applied = []
    with engine.begin() as conn:
        version = _version(conn)

        # Views first: create_all() skips names that already resolve to a view.
        create_views(conn)

        if _has_table(conn, 'accounts'):
            applied += _add_columns(conn, 'accounts', ACCOUNT_COLUMNS)
        if _has_table(conn, 'transactions'):
            applied += _add_columns(conn, 'transactions', TRANSACTION_COLUMNS)
            conn.execute(text(
                "UPDATE transactions SET source = 'manual' "
                "WHERE source IS NULL OR source = ''"
            ))

        for stmt in INDEXES:
            try:
                conn.execute(text(stmt))
            except Exception:
                # Duplicate data can block a unique index; leave it for the
                # dedupe command rather than failing startup.
                pass

        if version < CURRENT_VERSION:
            conn.execute(text('INSERT INTO schema_version (version, applied_at) VALUES (:v, :t)'),
                         {'v': CURRENT_VERSION, 't': datetime.utcnow()})

    if applied:
        print(f"Applied schema columns to {db_path}: {', '.join(applied)}")
    engine.dispose()
    return applied

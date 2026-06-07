"""
db.py — ClassFlow Watcher
─────────────────────────────────────────────────────────────────────────────
Industry-grade database layer using psycopg2 connection pooling.

Features
────────
- ThreadedConnectionPool — reuses connections instead of opening a new one
  on every request (the #1 performance killer in the old code).
- Context manager helpers — connections are always returned to the pool
  even if an exception is raised.
- Schema migration via init_db() — idempotent, safe to run on every startup.
- Parameterised queries everywhere — no SQL injection surface.
- Full index coverage for common query patterns.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Connection pool (initialised on first call to get_pool())
# ─────────────────────────────────────────────────────────────────────────────

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    """Return the global connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        db_url = os.getenv("DATABASE_URL", "").strip()
        if not db_url:
            raise EnvironmentError(
                "DATABASE_URL environment variable is not set. "
                "Example: postgresql://postgres:password@localhost:5432/classflow"
            )
        min_conn = int(os.getenv("DB_POOL_MIN", "1"))
        max_conn = int(os.getenv("DB_POOL_MAX", "10"))
        _pool = psycopg2.pool.ThreadedConnectionPool(min_conn, max_conn, db_url)
        logger.info(f"DB pool created (min={min_conn}, max={max_conn})")
    return _pool


@contextmanager
def get_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager: borrow a connection from the pool, yield it,
    then return it (even on exception).

    Usage:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(...)
    """
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Main assignments table
CREATE TABLE IF NOT EXISTS assignments (
    id                TEXT PRIMARY KEY,
    subject           TEXT        NOT NULL,
    title             TEXT        NOT NULL,
    due_date          DATE,
    is_completed      BOOLEAN     NOT NULL DEFAULT FALSE,
    summary           TEXT,
    difficulty        INTEGER     CHECK (difficulty BETWEEN 1 AND 10),
    estimated_minutes INTEGER     CHECK (estimated_minutes > 0),
    classification    TEXT        CHECK (classification IN (
                          'Assignment', 'CIE', 'Practical', 'Project', 'Other'
                      )),
    model_used        TEXT,
    ai_success        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Trigger: keep updated_at fresh on every UPDATE
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_assignments_updated_at'
    ) THEN
        CREATE TRIGGER trg_assignments_updated_at
        BEFORE UPDATE ON assignments
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    END IF;
END;
$$;

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_assignments_subject
    ON assignments(LOWER(subject));

CREATE INDEX IF NOT EXISTS idx_assignments_due_date
    ON assignments(due_date ASC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_assignments_completed
    ON assignments(is_completed);

CREATE INDEX IF NOT EXISTS idx_assignments_classification
    ON assignments(classification);

-- Composite index for the most common combined query
CREATE INDEX IF NOT EXISTS idx_assignments_subject_completed
    ON assignments(LOWER(subject), is_completed);
"""

# Additive migrations — safe to run on existing tables.
# Each statement uses IF NOT EXISTS / DO NOTHING patterns.
MIGRATION_SQL = [
    "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS model_used TEXT",
    "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS ai_success BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
]


def init_db() -> None:
    """
    Create schema + run additive migrations. Safe to call on every startup.
    New columns are added with IF NOT EXISTS — idempotent for existing DBs.
    """
    logger.info("Initialising database schema …")
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Create tables / triggers / indexes
            cur.execute(SCHEMA_SQL)
            # Additive column migrations (safe on existing tables)
            for sql in MIGRATION_SQL:
                try:
                    cur.execute(sql)
                except Exception as e:
                    logger.debug(f"Migration skip (already exists?): {sql[:60]} — {e}")
                    conn.rollback()
                    # Re-open cursor after rollback
                    cur = conn.cursor()
        conn.commit()
    logger.info("Database schema ready. ✅")


def check_db_health() -> bool:
    """Quick ping to verify the DB is reachable. Returns True/False."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return False

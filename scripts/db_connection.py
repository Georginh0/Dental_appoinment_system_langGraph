"""
db_connection.py — PostgreSQL / Supabase Connection Manager
============================================================
Replaces the MySQL connector. Supabase uses PostgreSQL under the hood.

USAGE:
    from scripts.db_connection import DBManager

    with DBManager() as db:
        rows  = db.query("SELECT * FROM doctors WHERE specialization = %s", ("orthodontist",))
        one   = db.query_one("SELECT * FROM patients WHERE patient_id = %s", (1000048,))
        rowid = db.execute("INSERT INTO appointments (...) VALUES (%s, %s)", (...))

SETUP:
    Add to .env:
        DATABASE_URL="postgresql://postgres.cizfjikkjhziqizkvvbb:sYfhih-rexxej-4xigra@aws-1-eu-west-2.pooler.supabase.com:5432/postgres"

    Get this string from:
        Supabase → Project Settings → Database → Connection string (Transaction mode)
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("dentai.db")

# ── Connection ─────────────────────────────────────────────

def _get_connection() -> psycopg2.extensions.connection:
    """
    Open a raw psycopg2 connection from DATABASE_URL.
    Supabase transaction-mode pooler (port 6543) is recommended for serverless.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise EnvironmentError(
            "\n  DATABASE_URL not set.\n"
            "  Add to .env:\n"
            "    DATABASE_URL=\"postgresql://postgres.cizfjikkjhziqizkvvbb:sYfhih-rexxej-4xigra@aws-1-eu-west-2.pooler.supabase.com:5432/postgres\"\n"
            "  Get it from: Supabase → Project Settings → Database → Connection string\n"
        )
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


# ── Context-manager wrapper ────────────────────────────────

class DBManager:
    """
    Context manager that wraps a single psycopg2 connection.

    • Auto-commits on clean exit
    • Auto-rollbacks on exception
    • Returns rows as plain dicts (RealDictCursor)

    Example:
        with DBManager() as db:
            rows = db.query("SELECT * FROM doctors")
    """

    def __init__(self) -> None:
        self._conn: Optional[psycopg2.extensions.connection] = None

    def __enter__(self) -> "DBManager":
        self._conn = _get_connection()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._conn:
            if exc_type:
                self._conn.rollback()
                log.error("DB rollback due to: %s — %s", exc_type.__name__, exc_val)
            else:
                self._conn.commit()
            self._conn.close()
        return False  # never swallow exceptions

    # ── Query helpers ───────────────────────────────────────

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute SELECT → list of row dicts."""
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Execute SELECT → single row dict or None."""
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        """
        Execute INSERT / UPDATE / DELETE.
        Returns lastrowid for INSERT (via RETURNING id), else rowcount.
        """
        with self._conn.cursor() as cur:
            # Append RETURNING id for INSERT statements so callers get the new PK
            if sql.strip().upper().startswith("INSERT") and "RETURNING" not in sql.upper():
                cur.execute(sql + " RETURNING id", params)
                row = cur.fetchone()
                return dict(row)["id"] if row else 0
            else:
                cur.execute(sql, params)
                return cur.rowcount

    def executemany(self, sql: str, data: list[tuple]) -> int:
        """Batch insert / update."""
        with self._conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, data, page_size=500)
            return cur.rowcount


# ── Quick connectivity test ────────────────────────────────

def test_connection() -> bool:
    """Run from terminal: python -c 'from scripts.db_connection import test_connection; test_connection()'"""
    try:
        with DBManager() as db:
            row = db.query_one("SELECT version() AS v, current_database() AS db")
            print(f"  Connected  |  {row['v'][:40]}  |  DB: {row['db']}")
            tables = db.query(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
            names = [t["tablename"] for t in tables]
            print(f"  Tables: {', '.join(names) if names else 'none — run 01_supabase_setup.sql'}")
        return True
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return False


if __name__ == "__main__":
    test_connection()

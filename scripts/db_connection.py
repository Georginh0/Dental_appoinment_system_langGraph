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

    Get this string from:
        Supabase → Project Settings → Database → Connection string (Transaction mode)
"""

from __future__ import annotations
 
import logging
import os
from typing import Optional
 
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
 
load_dotenv()
 
log = logging.getLogger("dentai.db")
 
 
def _get_connection() -> psycopg2.extensions.connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise EnvironmentError(
            "\n  DATABASE_URL not set in .env\n"
            "  Add: DATABASE_URL=postgresql://postgres.PROJ:[PW]@HOST:6543/postgres\n"
            "  Get it: Supabase → Project Settings → Database → Connection string\n"
        )
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
 
 
class DBManager:
    """
    Context manager wrapping a single psycopg2 connection.
    Auto-commits on clean exit, auto-rollbacks on exception.
    All rows returned as plain dicts (RealDictCursor).
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
        return False
 
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
 
        Returns:
          - If SQL contains RETURNING clause: the first integer value in the
            returned row (i.e. the generated PK such as id).
          - Otherwise: rowcount.
 
        Callers that need the generated PK must add RETURNING id to their SQL.
        The conversation_sessions table uses session_id (VARCHAR) as PK and
        does NOT have an id column — do NOT add RETURNING id for that table.
        """
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
 
            if "RETURNING" in sql.upper():
                row = cur.fetchone()
                if row:
                    # Return the first integer value found (the generated PK)
                    for val in dict(row).values():
                        if isinstance(val, int):
                            return val
                return 0
 
            return cur.rowcount
 
    def executemany(self, sql: str, data: list[tuple]) -> int:
        """Batch insert / update via execute_batch (500-row pages)."""
        with self._conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, data, page_size=500)
            return cur.rowcount
 
 
def test_connection() -> bool:
    """Quick connectivity test. Run: python -m scripts.db_connection"""
    try:
        with DBManager() as db:
            row = db.query_one("SELECT version() AS v, current_database() AS db")
            print(f"  Connected  |  {row['v'][:45]}  |  DB: {row['db']}")
            tables = db.query(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
            names = [t["tablename"] for t in tables]
            print(f"  Tables: {', '.join(names) if names else 'NONE — run 01_supabase_setup.sql'}")
        return True
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return False
 
 
if __name__ == "__main__":
    test_connection()
 
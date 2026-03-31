"""
============================================================
  db_connection.py — MySQL Connection Manager
  DentAI Pro | Shared by all scripts

  USAGE:
    from scripts.db_connection import get_db, DBManager

    # Simple connection
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    # Context manager (auto-closes)
    with DBManager() as db:
        results = db.query("SELECT * FROM doctors")
============================================================
"""

import os
import sys
import logging
from typing import Any, Optional
from contextlib import contextmanager
import mysql.connector
from mysql.connector import Error, MySQLConnection
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("dentai.db")

# ── Connection config ──────────────────────────────────────
# These read from your .env file first, then fall back to defaults.
# Create a .env file in the project root:
#
#   MYSQL_HOST=localhost
#   MYSQL_PORT=3306
#   MYSQL_USER=root
#   MYSQL_PASSWORD=your_password_here
#   MYSQL_DATABASE=dentai_pro

DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "dentai_pro"),
    "charset": "utf8mb4",
    "autocommit": False,
    "connection_timeout": 10,
}


def get_db() -> MySQLConnection:
    """
    Return a live MySQL connection.

    Example:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM doctors")
        rows = cursor.fetchall()
        conn.close()
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        log.error(f"MySQL connection failed: {e}")
        raise ConnectionError(
            f"Cannot connect to MySQL.\n"
            f"Host: {DB_CONFIG['host']}:{DB_CONFIG['port']}\n"
            f"Database: {DB_CONFIG['database']}\n"
            f"Error: {e}\n\n"
            f"Fix:\n"
            f"  1. Is MySQL running? (mysql.server start OR XAMPP)\n"
            f"  2. Check your .env file has the correct password\n"
            f"  3. Did you run 01_mysql_setup.sql?\n"
        )


class DBManager:
    """
    Context-manager wrapper for MySQL connections.
    Automatically commits on success, rolls back on error, closes on exit.

    Usage:
        with DBManager() as db:
            results = db.query("SELECT * FROM doctors WHERE specialization=%s",
                               ("orthodontist",))
            db.execute("UPDATE doctor_availability SET is_available=%s WHERE slot_id=%s",
                       (False, 42))
    """

    def __init__(self):
        self.conn: Optional[MySQLConnection] = None

    def __enter__(self):
        self.conn = get_db()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type:
                self.conn.rollback()
                log.error(f"DB transaction rolled back due to: {exc_val}")
            else:
                self.conn.commit()
            self.conn.close()
        return False  # Don't suppress exceptions

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT and return list of row dicts."""
        cursor = self.conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Execute a SELECT and return single row dict or None."""
        cursor = self.conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        row = cursor.fetchone()
        cursor.close()
        return row

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Execute INSERT/UPDATE/DELETE, return lastrowid or rowcount."""
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        result = cursor.lastrowid or cursor.rowcount
        cursor.close()
        return result

    def executemany(self, sql: str, data: list[tuple]) -> int:
        """Execute batch INSERT/UPDATE, return rowcount."""
        cursor = self.conn.cursor()
        cursor.executemany(sql, data)
        count = cursor.rowcount
        cursor.close()
        return count

    def call_proc(self, proc_name: str, args: tuple) -> tuple:
        """Call a stored procedure and return output args."""
        cursor = self.conn.cursor()
        result = cursor.callproc(proc_name, args)
        cursor.close()
        return result


def test_connection() -> bool:
    """Quick connectivity test — run this to verify your setup."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION(), DATABASE()")
        version, db = cursor.fetchone()
        print(f"✅ Connected! MySQL {version} | Database: {db}")
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        print(
            f"   Tables found: {', '.join(tables) if tables else 'NONE (run 01_mysql_setup.sql!)'}"
        )
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        return False


if __name__ == "__main__":
    test_connection()

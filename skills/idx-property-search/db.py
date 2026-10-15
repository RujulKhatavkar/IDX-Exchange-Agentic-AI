"""MySQL connection pool and parameterized query execution.

Credentials come from the repo-root .env (MYSQL_HOST, MYSQL_USER,
MYSQL_PASSWORD, MYSQL_DATABASE), loaded without a third-party dependency
since this project otherwise has none at runtime.
"""

import os
from pathlib import Path

import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_pool: MySQLConnectionPool | None = None


def _load_env_file() -> None:
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _get_pool() -> MySQLConnectionPool:
    global _pool
    if _pool is None:
        _load_env_file()
        _pool = mysql.connector.pooling.MySQLConnectionPool(
            pool_name="idx_exchange_pool",
            pool_size=5,
            host=os.environ.get("MYSQL_HOST", "localhost"),
            user=os.environ["MYSQL_USER"],
            password=os.environ.get("MYSQL_PASSWORD", ""),
            database=os.environ.get("MYSQL_DATABASE", "idx_exchange"),
        )
    return _pool


def run_query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a parameterized SELECT and return rows as a list of dicts.

    `sql` must only ever come from this package's own query builders -
    never from string-joined user input - since %s placeholders are the
    only thing standing between a query and SQL injection.
    """
    conn = _get_pool().get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()

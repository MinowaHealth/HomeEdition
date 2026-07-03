"""Live test Postgres helpers: app-role connection + count helpers."""
from __future__ import annotations

from typing import Any

# livetest/__init__.py puts UserApp/webapp on sys.path, so db_driver is
# importable here (same shim the webapp uses).
import db_driver
from db_driver import sql

from livetest.config import LiveTestConfig


def open_rls_connection(cfg: LiveTestConfig, user_id: str) -> Any:
    """Open a connection as ``cfg.app_db_user`` via the db_driver shim.

    Home Edition: no RLS — the name is kept for the ~40 flow call sites.
    Verification queries must scope by tenant_id/user_id themselves.
    ``user_id`` is accepted (and ignored) so call sites stay unchanged.

    Autocommit is deliberate: flows interleave reads with the webapp's
    writes, and an aborted implicit transaction would mask later errors.
    """
    conn = db_driver.connect(
        host=cfg.db_host,
        port=cfg.db_port,
        dbname=cfg.db_name,
        user=cfg.app_db_user,
        password=cfg.app_db_password,
    )
    conn.autocommit = True
    return conn


def count_rows(cur: Any, table: str, where_sql: str = "",
               params: tuple = ()) -> int:
    """SELECT count(*) FROM {table} [WHERE ...]. Table name is passed
    through ``sql.Identifier`` (db_driver shim, identical surface on both
    drivers) to prevent injection. where_sql is a pre-composed WHERE
    clause body (no 'WHERE' keyword) with %s placeholders that bind
    against params."""
    query = sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
    if where_sql:
        query = sql.SQL("{} WHERE {}").format(query, sql.SQL(where_sql))
    cur.execute(query, params)
    row = cur.fetchone()
    return int(row["n"]) if row else 0


def fetch_row_by_id(cur: Any, table: str, row_id: str) -> Any:
    """SELECT * FROM {table} WHERE id = %s LIMIT 1.

    Returns the dict-style row from the shim's row factory (or None).
    Caller treats it as ``dict[str, Any] | None``.
    """
    query = sql.SQL("SELECT * FROM {} WHERE id = %s LIMIT 1").format(
        sql.Identifier(table)
    )
    cur.execute(query, (row_id,))
    return cur.fetchone()

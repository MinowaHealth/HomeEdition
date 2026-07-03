"""Database driver helpers (psycopg3).

Centralizes psycopg3 import + a small set of helpers callers reach for
(``connect``, ``make_pool``, ``transaction``, ``executemany_rows``,
``set_session_var`` / ``reset_session_var``, ``commit_after_reset``,
``register_pgvector``).
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Generator, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# psycopg3 imports + error re-exports
# ---------------------------------------------------------------------------
import psycopg
from psycopg import sql  # SQL composition (Identifier, SQL, Composed, …)
from psycopg import Error  # PEP-249 root error class
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from psycopg.errors import (
    QueryCanceled,
    UniqueViolation,
    IntegrityError,
    UndefinedTable,
    InsufficientPrivilege,
    OperationalError,
)


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------
def connect(**kwargs: Any) -> Any:
    """Open a database connection with a dict-style row factory.

    The ``row_factory`` keyword is set internally — callers must not pass it.

    Returns ``Any`` so callers can use ``row['col']`` without pyright
    complaining that the underlying ``Connection`` exposes ``TupleRow``-typed
    cursors. The runtime row factory makes every cursor return ``dict``-like
    rows.
    """
    kwargs.pop("cursor_factory", None)
    kwargs.pop("row_factory", None)

    # psycopg3 takes ``dbname`` (not ``database``); accept both forms.
    if "database" in kwargs and "dbname" not in kwargs:
        kwargs["dbname"] = kwargs.pop("database")
    return psycopg.connect(row_factory=dict_row, **kwargs)


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------
def make_pool(minconn: int, maxconn: int, **kwargs: Any) -> "ConnectionPool":
    """Build a psycopg3 ``ConnectionPool`` with a dict-row factory.

    The pool exposes psycopg3's native ``getconn``/``putconn``/``close``
    surface. ``ConnectionPool.putconn`` auto-rolls-back any open
    transaction, so callers needing a RESET to persist must call
    ``commit_after_reset(conn)`` before returning the connection.
    The ``row_factory`` keyword is set internally.
    """
    kwargs.pop("cursor_factory", None)
    kwargs.pop("row_factory", None)

    # psycopg3 takes ``dbname`` rather than ``database``.
    if "database" in kwargs and "dbname" not in kwargs:
        kwargs["dbname"] = kwargs.pop("database")

    return ConnectionPool(
        min_size=minconn,
        max_size=maxconn,
        kwargs={"row_factory": dict_row, **kwargs},
    )


# ---------------------------------------------------------------------------
# pgvector + UUID adapter dispatch
# ---------------------------------------------------------------------------
def register_pgvector(conn) -> None:
    """Register the pgvector type adapter on the given connection."""
    from pgvector.psycopg import register_vector
    register_vector(conn)


# ---------------------------------------------------------------------------
# Bulk write
# ---------------------------------------------------------------------------
_VALUES_PLACEHOLDER_RE = re.compile(r"\bVALUES\s+%s\b", re.IGNORECASE)


def executemany_rows(cur, sql: str, rows: Sequence[Sequence[Any]]) -> None:
    """Bulk-write ``rows`` via ``executemany``.

    Accepts the legacy ``execute_values`` SQL form (``INSERT ... VALUES %s
    [...]``). Rewrites the ``VALUES %s`` token into per-row placeholders
    (``VALUES (%s, %s, ...)``) based on the arity of the first row, then
    calls ``cur.executemany``. ON CONFLICT and other trailing clauses are
    preserved.

    No-op when ``rows`` is empty.
    """
    if not rows:
        return

    arity = len(rows[0])
    placeholders = "(" + ", ".join(["%s"] * arity) + ")"
    rewritten, n = _VALUES_PLACEHOLDER_RE.subn(
        f"VALUES {placeholders}", sql, count=1
    )
    if n != 1:
        raise ValueError(
            "executemany_rows expected SQL with a single 'VALUES %s' token; "
            f"got: {sql!r}"
        )
    cur.executemany(rewritten, rows)


def executemany_simple(cur, sql: str, rows: Sequence[Sequence[Any]]) -> None:
    """Bulk-write ``rows`` using an explicit-placeholder SQL form.

    Accepts SQL with explicit per-row placeholders, e.g.
    ``INSERT INTO foo (a, b) VALUES (%s, %s)``. Calls ``cur.executemany``
    (pipelined under the hood on psycopg3).

    No-op when ``rows`` is empty.

    Use ``executemany_rows`` for the ``execute_values`` form
    (``VALUES %s``); they are NOT interchangeable.
    """
    if not rows:
        return
    cur.executemany(sql, rows)


# ---------------------------------------------------------------------------
# Transaction context manager
# ---------------------------------------------------------------------------
@contextmanager
def transaction(conn) -> Generator[None, None, None]:
    """Driver-agnostic transaction block.

    Replaces the ambiguous ``with conn:`` pattern, which on psycopg3 means
    "close connection" (not "transaction"). Use::

        with db_driver.transaction(conn):
            with conn.cursor() as cur:
                cur.execute(...)
        # commits on success, rolls back on exception
    """
    with conn.transaction():
        yield


# ---------------------------------------------------------------------------
# Session variable helpers (SET / RESET)
# ---------------------------------------------------------------------------
def set_session_var(cur, name: str, value: str, local: bool = False) -> None:
    """Set a Postgres session variable.

    Equivalent to ``SET name = value`` (or ``SET LOCAL`` if ``local=True``),
    but issued via ``set_config`` so it works under psycopg3's
    extended-query protocol. Postgres rejects parameterized ``SET`` over
    extended protocol; ``set_config`` is a regular function call so
    parameter binding works.

    Args:
        cur: Active cursor.
        name: Setting name, e.g. ``"ivfflat.probes"``.
        value: Setting value (always passed as a string).
        local: True → SET LOCAL (transaction-scoped); False (default) →
            SET (session-scoped).
    """
    cur.execute(
        "SELECT set_config(%s, %s, %s)",
        (name, str(value), local),
    )


# ---------------------------------------------------------------------------
# Pool-return cleanup helper
# ---------------------------------------------------------------------------
def commit_after_reset(conn) -> None:
    """Persist ``RESET statement_timeout`` before pool return.

    ``ConnectionPool.putconn`` auto-rolls-back any open transaction — which
    would roll back the RESET itself, leaving the prior request's timeout
    on the connection. Calling this after the RESET durably persists it.
    """
    try:
        conn.commit()
    except Exception as e:
        logger.error("commit_after_reset failed: %s", e)


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------
def driver_info() -> dict[str, str]:
    """Return a small dict identifying the active driver."""
    return {"driver": "psycopg3", "version": getattr(psycopg, "__version__", "?")}


__all__ = [
    "sql",
    "Error",
    "QueryCanceled",
    "UniqueViolation",
    "IntegrityError",
    "UndefinedTable",
    "InsufficientPrivilege",
    "OperationalError",
    "connect",
    "make_pool",
    "register_pgvector",
    "executemany_rows",
    "executemany_simple",
    "transaction",
    "set_session_var",
    "commit_after_reset",
    "driver_info",
]

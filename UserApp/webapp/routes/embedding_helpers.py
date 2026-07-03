"""Shared inline embedding helper for v2 routes.

Generalizes the _embed_observation pattern from vitals.py to work with
any embedding-enabled table (health_inputs, health_food_itemsv2, health_observations).

Used by:
- routes/health_inputs_v2.py (embed name on create/update)
- routes/food_v2.py (embed name on create/update)
- routes/vitals_v2.py (embed observation content on create/update)
"""
import logging
from typing import Optional

from flask import current_app
from db_driver import sql

from utils import table_has_column

logger = logging.getLogger(__name__)


def embed_field(
    conn,
    tenant_id: int,
    record_id,
    table: str,
    embed_column: str,
    content: str,
    client_embedding: Optional[list] = None,
) -> Optional[str]:
    """Generic inline embedding. Accepts client vector or generates server-side.

    Called AFTER the main INSERT/UPDATE has been committed. Uses its own
    cursor to avoid poisoning the caller's transaction. Fails silently —
    embedding failure never blocks CRUD.

    Args:
        conn: Active database connection (app-level user_id scoping; no RLS).
        tenant_id: Current tenant.
        record_id: UUID of the row to update.
        table: Table name (must be in EMBEDDING_TABLES whitelist).
        embed_column: Column name for the vector (e.g., 'embedding_name').
        content: Text to embed if no client_embedding provided.
        client_embedding: Optional pre-computed 768-dim float array from device.

    Returns:
        'client' if client embedding used, 'server' if server-side,
        None if embedding was skipped or failed.
    """
    try:
        if not table_has_column(conn, table, embed_column):
            return None  # Column not yet migrated

        from embedding_utils import (
            get_embedding,
            validate_embedding_vector,
            register_pgvector,
            EMBEDDING_DIMENSIONS,
        )

        embedding_to_store = None
        source = None

        # 1. Prefer client-provided embedding if valid
        if client_embedding is not None:
            if validate_embedding_vector(client_embedding, EMBEDDING_DIMENSIONS):
                embedding_to_store = client_embedding
                source = 'client'
            else:
                current_app.logger.warning(
                    "embed_field_invalid_client table=%s record=%s dim=%s",
                    table, record_id,
                    len(client_embedding) if isinstance(client_embedding, list) else 'N/A',
                )

        # 2. Fall back to server-side generation
        if embedding_to_store is None and content and content.strip():
            register_pgvector(conn)
            embedding_to_store = get_embedding(content)
            source = 'server' if embedding_to_store else None

        # 3. Store if we have a vector
        if embedding_to_store is not None:
            register_pgvector(conn)
            cur = conn.cursor()
            cur.execute(
                sql.SQL(
                    "UPDATE {table} SET {embed_column} = %s::vector "
                    "WHERE tenant_id = %s AND id = %s"
                ).format(
                    table=sql.Identifier(table),
                    embed_column=sql.Identifier(embed_column),
                ),
                (str(embedding_to_store), tenant_id, record_id),
            )
            cur.close()
            conn.commit()
            return source

        return None

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        current_app.logger.warning(
            "embed_field_failed table=%s record=%s error=%s",
            table, record_id, exc,
        )
        return None

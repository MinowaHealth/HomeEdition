#!/usr/bin/env python3
"""Backfill missing pgvector embeddings across all EMBEDDING_TABLES.

Fill-NULL only, naturally resumable: candidate rows are `WHERE <embed_column>
IS NULL`, so a crashed or interrupted run picks up where it left off. To
REGENERATE everything (e.g. after an embedding-model change), first null the
embedding columns, then run this.

Home Edition: the app-role connection carries no RLS, so every row read and
vector write scopes explicitly by tenant_id/user_id (household trust model).
An admin connection reads only user ids to drive the per-user loop.

Identical source texts are embedded once per run (med/food names repeat
heavily), cached across users and tables.

Run inside the webapp container:

    docker exec -e PYTHONPATH=/app healthv10-web \
        python3 scripts/backfill_embeddings.py [--table T ...] [--dry-run]

Exit code 1 if any embedding failed (rerun to retry just those rows).
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/app")

import db_manager  # noqa: E402
from db_driver import sql  # noqa: E402
from embedding_utils import (  # noqa: E402
    CONTENT_BUILDERS,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_TABLES,
    get_embedding,
    register_pgvector,
    validate_embedding_vector,
)

# Live columns to SELECT for content_builder tables (single-text tables use
# text_column). condition_to_text expects description (live column is notes),
# so source_text adapts rows into the shape the builder reads.
BUILDER_COLS = {
    "health_allergies": ["allergen", "reaction", "notes"],
    "health_conditions": ["name", "notes"],
}


def source_text(table: str, cfg: dict, row: dict) -> str:
    if table == "health_conditions":
        row = {"name": row.get("name"), "description": row.get("notes")}
    if cfg["content_builder"]:
        return CONTENT_BUILDERS[cfg["content_builder"]](row) or ""
    return row.get(cfg["text_column"]) or ""


def backfill_table(conn, table: str, cfg: dict, cache: dict, dry_run: bool,
                   user_id: str, tenant_id: int):
    """Fill NULL embeddings for one table, scoped to one user.

    Returns (candidates, filled, skipped_empty, failed).
    """
    cols = BUILDER_COLS.get(table) or [cfg["text_column"]]
    where = sql.SQL("tenant_id = %s AND user_id = %s AND {e} IS NULL").format(
        e=sql.Identifier(cfg["embed_column"]))
    if table == "documents":
        where = sql.SQL("{w} AND deleted_at IS NULL").format(w=where)

    cur = conn.cursor()
    cur.execute(
        sql.SQL("SELECT tenant_id, id, {cols} FROM {t} WHERE {w}").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)),
            t=sql.Identifier(table),
            w=where,
        ),
        (tenant_id, user_id),
    )
    rows = cur.fetchall()

    filled = skipped = failed = 0
    for row in rows:
        text = source_text(table, cfg, row).strip()
        if not text:
            skipped += 1
            continue
        if dry_run:
            filled += 1
            continue
        vec = cache.get(text)
        if vec is None:
            vec = get_embedding(text)
            if not (vec and validate_embedding_vector(vec, EMBEDDING_DIMENSIONS)):
                failed += 1
                continue
            cache[text] = vec
        cur.execute(
            sql.SQL(
                "UPDATE {t} SET {e} = %s::vector "
                "WHERE tenant_id = %s AND user_id = %s AND id = %s"
            ).format(t=sql.Identifier(table), e=sql.Identifier(cfg["embed_column"])),
            (str(vec), row["tenant_id"], user_id, row["id"]),
        )
        filled += 1
    if not dry_run:
        conn.commit()
    cur.close()
    return len(rows), filled, skipped, failed


def all_user_ids() -> list[tuple]:
    """(user_id, tenant_id) for every account — ids only, via admin read."""
    conn = db_manager.get_direct_admin_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, tenant_id FROM users ORDER BY created_at")
        return [(str(r["id"]), r["tenant_id"]) for r in cur.fetchall()]
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", action="append", choices=sorted(EMBEDDING_TABLES),
                    help="limit to specific table(s); default all")
    ap.add_argument("--dry-run", action="store_true",
                    help="count candidates only — no Ollama calls, no writes")
    args = ap.parse_args()

    tables = {t: c for t, c in EMBEDDING_TABLES.items()
              if not args.table or t in args.table}

    cache: dict = {}
    totals: dict = {}
    for user_id, tenant_id in all_user_ids():
        with db_manager.get_direct_connection_for_user(user_id, tenant_id) as conn:
            register_pgvector(conn)
            for table, cfg in tables.items():
                n, filled, skipped, failed = backfill_table(
                    conn, table, cfg, cache, args.dry_run, user_id, tenant_id)
                if n:
                    t = totals.setdefault(table, [0, 0, 0])
                    t[0] += filled
                    t[1] += skipped
                    t[2] += failed

    any_failed = False
    label = "candidates" if args.dry_run else "filled"
    for table, (filled, skipped, failed) in sorted(totals.items()):
        print(f"{table}: {label}={filled} empty_text={skipped} failed={failed}")
        any_failed = any_failed or failed
    if not totals:
        print("nothing to do — no rows with NULL embeddings")
    print(f"unique texts embedded: {len(cache)}")
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())

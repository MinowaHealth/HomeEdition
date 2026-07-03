"""Database operations for OCR worker.

Writes per-page OCR results to document_pages and updates the parent
document record. Uses the healthv10_app role; every statement scopes
by tenant_id + user_id at the application level (Home Edition has no
RLS).

Driver: routed through the ``db_driver`` shim. The shim provides
``executemany_rows`` for the bulk page-results insert and
``transaction(conn)`` for transaction blocks (see the psycopg3
conventions in DataModel3/README.md).
"""

import logging
import os

import db_driver
from .engine import PageResult
from .quality import confidence_to_label, worst_label

log = logging.getLogger(__name__)

APP_DB_PASSWORD = os.environ.get("APP_DB_PASSWORD")
if not APP_DB_PASSWORD:
    raise ValueError(
        "APP_DB_PASSWORD must be set. Refusing to start without app "
        "credentials to prevent running as superuser."
    )

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "pgvector"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_NAME", "healthv10"),
    "user": os.environ.get("APP_DB_USER", "healthv10_app"),
    "password": APP_DB_PASSWORD,
}


def get_connection():
    """Create a new database connection."""
    return db_driver.connect(**DB_CONFIG)


def update_ocr_status(tenant_id: int, user_id: str, document_id: str, status: str) -> None:
    """Update documents.ocr_status (e.g., 'processing', 'complete', 'failed').

    Args:
        tenant_id: Document's tenant.
        user_id: Document owner.
        document_id: Document UUID.
        status: New ocr_status value.
    """
    conn = get_connection()
    try:
        with db_driver.transaction(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE documents
                       SET ocr_status = %s, updated_at = now()
                       WHERE tenant_id = %s AND user_id = %s AND id = %s""",
                    (status, tenant_id, user_id, document_id),
                )
        log.info("Set ocr_status=%s for document %s", status, document_id)
    finally:
        conn.close()


def save_page_results(
    tenant_id: int,
    user_id: str,
    document_id: str,
    pages: list[PageResult],
) -> None:
    """Write per-page OCR results and update the parent document.

    Inserts rows into document_pages and updates the documents table
    with aggregated results (full text, quality label, page count).

    Args:
        tenant_id: Document's tenant.
        user_id: Document owner UUID.
        document_id: Parent document UUID.
        pages: List of PageResult from OCR processing.
    """
    conn = get_connection()
    try:
        with db_driver.transaction(conn):
            with conn.cursor() as cur:
                # Insert page results
                page_values = [
                    (
                        tenant_id,
                        document_id,
                        user_id,
                        p.page_number,
                        p.text,
                        p.confidence,
                        confidence_to_label(p.confidence),
                        p.image_path,
                    )
                    for p in pages
                ]

                db_driver.executemany_rows(
                    cur,
                    """INSERT INTO document_pages
                       (tenant_id, document_id, user_id, page_number,
                        ocr_text, ocr_confidence, quality_label, image_path)
                       VALUES %s
                       ON CONFLICT (tenant_id, document_id, page_number)
                       DO UPDATE SET
                           ocr_text = EXCLUDED.ocr_text,
                           ocr_confidence = EXCLUDED.ocr_confidence,
                           quality_label = EXCLUDED.quality_label,
                           image_path = EXCLUDED.image_path""",
                    page_values,
                )

                # Aggregate results for parent document
                full_text = "\n\n".join(p.text for p in pages if p.text)
                page_labels = [confidence_to_label(p.confidence) for p in pages]
                doc_label = worst_label(page_labels)

                cur.execute(
                    """UPDATE documents
                       SET ocr_status = 'complete',
                           ocr_text_full = %s,
                           quality_label = %s,
                           page_count = %s,
                           updated_at = now()
                       WHERE tenant_id = %s AND user_id = %s AND id = %s""",
                    (full_text, doc_label, len(pages), tenant_id, user_id, document_id),
                )

        log.info(
            "Saved OCR results: document=%s, pages=%d, label=%s",
            document_id,
            len(pages),
            doc_label,
        )
    finally:
        conn.close()


def mark_failed(tenant_id: int, user_id: str, document_id: str, error: str) -> None:
    """Mark a document as OCR failed.

    Args:
        tenant_id: Document's tenant.
        user_id: Document owner.
        document_id: Document UUID.
        error: Error message for logging.
    """
    conn = get_connection()
    try:
        with db_driver.transaction(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE documents
                       SET ocr_status = 'failed', updated_at = now()
                       WHERE tenant_id = %s AND user_id = %s AND id = %s""",
                    (tenant_id, user_id, document_id),
                )
        log.error("OCR failed for document %s: %s", document_id, error)
    finally:
        conn.close()


def save_embedding(
    tenant_id: int,
    user_id: str,
    document_id: str,
    embedding: list[float],
    ocr_text_full: str,
) -> None:
    """Write the embedding vector and full OCR text to the documents table.

    Best-effort: called after OCR completes. The document is already marked
    'complete' by save_page_results; the embedding only enables semantic
    search and never gates document availability.
    """
    conn = get_connection()
    try:
        db_driver.register_pgvector(conn)
        with db_driver.transaction(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE documents
                       SET embedding_content = %s::vector,
                           ocr_text_full = %s,
                           updated_at = now()
                       WHERE tenant_id = %s AND user_id = %s AND id = %s""",
                    (embedding, ocr_text_full, tenant_id, user_id, document_id),
                )
        log.info("Saved embedding for document %s", document_id)
    finally:
        conn.close()

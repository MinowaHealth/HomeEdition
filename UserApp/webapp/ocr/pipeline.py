"""Inline OCR orchestration (Home Edition, Open Decision 6).

Runs the full document pipeline in-process — the work the enterprise
OCRWorker + embed_worker did across two RabbitMQ queues:

    processing -> render + Tesseract -> write document_pages
               -> mark document complete -> best-effort embedding

Intended to be invoked via ``background.fire_and_forget`` from the upload
handler so it runs in a daemon thread; the upload returns immediately and
the client polls ``documents.ocr_status``.
"""

import logging
import os

from . import db
from .engine import process_document

log = logging.getLogger(__name__)


def process_document_inline(
    tenant_id: int,
    user_id: str,
    document_id: str,
    file_path: str,
) -> None:
    """Render, OCR, persist, and embed a freshly-uploaded document.

    Never raises — failures are recorded as ``ocr_status='failed'`` so the
    document stays in the user's library and can be reprocessed. Embedding
    is best-effort and never gates document availability.

    Args:
        tenant_id: Document's tenant (always 1 in Home Edition).
        user_id: Document owner UUID.
        document_id: Document UUID.
        file_path: Absolute path to the stored original.
    """
    try:
        db.update_ocr_status(tenant_id, user_id, document_id, "processing")

        output_dir = os.path.dirname(file_path)
        pages = process_document(file_path, output_dir)

        if not pages:
            db.mark_failed(tenant_id, user_id, document_id, "OCR produced no page results")
            return

        # Writes document_pages and marks the document 'complete'.
        db.save_page_results(tenant_id, user_id, document_id, pages)

        # Best-effort semantic-search embedding of the full text.
        full_text = "\n\n".join(p.text for p in pages if p.text)
        if full_text.strip():
            _embed_best_effort(tenant_id, user_id, document_id, full_text)

        log.info("Inline OCR complete: document=%s pages=%d", document_id, len(pages))

    except Exception as exc:  # noqa: BLE001 — daemon thread, must not propagate
        log.error("Inline OCR failed for document %s: %s", document_id, exc, exc_info=True)
        try:
            db.mark_failed(tenant_id, user_id, document_id, str(exc))
        except Exception:  # noqa: BLE001
            log.error("Could not mark document %s failed", document_id, exc_info=True)


def _embed_best_effort(
    tenant_id: int,
    user_id: str,
    document_id: str,
    full_text: str,
) -> None:
    """Generate and store the document embedding; swallow all failures."""
    try:
        import embedding_utils

        vector = embedding_utils.get_embedding(full_text)
        if vector:
            db.save_embedding(tenant_id, user_id, document_id, vector, full_text)
        else:
            log.info("No embedding produced for document %s (Ollama unavailable?)", document_id)
    except Exception as exc:  # noqa: BLE001 — embedding never gates the document
        log.warning("Embedding skipped for document %s: %s", document_id, exc)

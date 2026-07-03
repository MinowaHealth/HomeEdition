"""In-process document OCR for Home Edition.

Replaces the enterprise OCRWorker + RabbitMQ pipeline with synchronous,
in-process Tesseract OCR (Open Decision 6). The upload handler runs
``process_document_inline`` in a background daemon thread so the HTTP
request returns immediately and the client polls ``ocr_status`` exactly
as it did under the old queue-based pipeline — no broker, no separate
container, no client-contract change.
"""

from .pipeline import process_document_inline

__all__ = ["process_document_inline"]

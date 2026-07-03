"""
get_document — a single document with its OCR pages and annotations.

Required argument: `document_id`. The endpoint returns:
  - document metadata (title, category, dates, filesize)
  - page-by-page OCR text with per-page confidence
  - any user annotations

Caller is typically responding to a search hit. Keep responses bounded:
default is metadata + page text only, and `include_annotations=false`
drops the annotations block for a tighter payload.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from mcp.types import Tool

from tools._envelope import build_envelope
from tools._shape import as_dict, as_list
from tools._sources import fetch_sources

logger = logging.getLogger(__name__)


def schema() -> Tool:
    return Tool(
        name="get_document",
        description=(
            "Return a document's metadata, OCR page text, and optional "
            "annotations. Use after search_my_data surfaces a document hit."
        ),
        inputSchema={
            "type": "object",
            "required": ["document_id"],
            "properties": {
                "document_id": {"type": "string"},
                "include_annotations": {"type": "boolean", "default": True},
            },
        },
    )


async def _safe(client: Any, path: str, **kwargs) -> Any:
    try:
        return await client.call_api(path, **kwargs)
    except Exception as exc:
        logger.warning(f"documents: {path} failed: {exc}")
        return {"_error": str(exc)}


async def handle(arguments: Dict[str, Any], client: Any) -> Dict[str, Any]:
    doc_id = arguments.get("document_id")
    include_ann = arguments.get("include_annotations", True)
    if not doc_id:
        return build_envelope(
            {},
            coverage={
                "counts": {"rows": 0, "sources_represented": []},
                "gaps": [{"reason": "document_id is required"}],
                "truncated": False,
            },
            sources=await fetch_sources(client),
        )

    if include_ann:
        doc_r, ann_r, sources = await asyncio.gather(
            _safe(client, f"/documents/{doc_id}", method="GET"),
            _safe(client, f"/documents/{doc_id}/annotations", method="GET"),
            fetch_sources(client),
        )
    else:
        doc_r, sources = await asyncio.gather(
            _safe(client, f"/documents/{doc_id}", method="GET"),
            fetch_sources(client),
        )
        ann_r = None

    gaps = []
    doc = as_dict(doc_r, where="documents.doc")
    if doc.get("_error"):
        gaps.append({"source": "documents", "reason": doc["_error"]})
        doc = {}

    annotations = []
    if include_ann:
        if isinstance(ann_r, dict) and ann_r.get("_error"):
            gaps.append({"source": "annotations", "reason": ann_r["_error"]})
        elif isinstance(ann_r, list):
            annotations = ann_r
        elif isinstance(ann_r, dict):
            annotations = ann_r.get("annotations") or ann_r.get("entries") or []

    pages = as_list(doc.get("pages"), where="documents.pages")

    data = {
        "document": {
            "id": doc.get("id") or doc_id,
            "title": doc.get("title"),
            "category": doc.get("category"),
            "created_at": doc.get("created_at"),
            "page_count": len(pages),
            "filesize": doc.get("filesize") or doc.get("bytes"),
        },
        "pages": pages,
        "annotations": annotations,
    }

    coverage = {
        "counts": {
            "rows": 1 if doc.get("id") or doc else 0,
            "pages": len(pages),
            "annotations": len(annotations),
            "sources_represented": ["manual"] if doc else [],
        },
        "gaps": gaps,
        "truncated": False,
    }

    return build_envelope(data, coverage=coverage, sources=sources)

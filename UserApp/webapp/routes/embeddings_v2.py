"""Embedding sync and semantic search routes — v2 passthrough.

All routes proxy to v1 implementations unchanged.
These endpoints already handle embeddings natively.
"""
from flask import Blueprint

from .embeddings import (
    sync_embeddings,
    semantic_search,
)

bp = Blueprint('embeddings_v2', __name__, url_prefix='/api/v2')

bp.add_url_rule('/sync-embeddings', 'sync_embeddings', sync_embeddings, methods=['POST'])
bp.add_url_rule('/semantic-search', 'semantic_search', semantic_search, methods=['POST'])

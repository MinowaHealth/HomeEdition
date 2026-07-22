"""
UserDocs — Document management routes.

Phase 0: Upload, list, detail, update metadata, soft-delete.
Phase 3: Annotation CRUD (create, list, update, delete).
"""
from flask import Blueprint, request, jsonify, g, current_app, send_file, redirect
from db_driver import sql
from werkzeug.utils import secure_filename
from datetime import datetime
from pathlib import Path
import hashlib
import mimetypes
import pytz
import uuid
import os

from utils import (
    require_auth,
    get_db_connection,
    get_user_id,
    local_to_utc,
    parse_pagination_params,
    paginated_response,
)
import db_manager
import analytics
from .embedding_helpers import embed_field

bp = Blueprint('documents', __name__, url_prefix='/api/v1')

# 5 MB upload limit (plan scope boundary)
MAX_DOCUMENT_SIZE = 5 * 1024 * 1024

ALLOWED_MIME_PREFIXES = (
    'application/pdf',
    'image/',
    'text/plain',
)

STORAGE_ROOT = Path(os.environ.get('USERDOCS_STORAGE_PATH', '/data/userdocs'))


def _doc_storage_dir(tenant_id: int, user_id: str, doc_id: str) -> Path:
    """Return the storage directory for a document: {root}/{tenant}/{user}/{doc}/"""
    return STORAGE_ROOT / str(tenant_id) / str(user_id) / str(doc_id)


def _is_allowed_mime(mime_type: str) -> bool:
    """Check if the MIME type is in the allowed set."""
    if not mime_type:
        return False
    return any(mime_type.startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES)


def _doc_links(doc_id: str) -> dict:
    """Session-gated view links. Relative paths — same-origin SPA uses them
    directly; UserMCP absolutizes with APP_BASE_URL."""
    return {
        'web': f'/?activity=documents&doc={doc_id}',
        'view': f'/api/v1/documents/{doc_id}/view',
        'download': f'/api/v1/documents/{doc_id}/download',
    }


def _sha256_file(path: Path) -> str:
    """Stream-hash a file on disk; returns hex digest."""
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ==================== UPLOAD ====================

@bp.route('/documents/upload', methods=['POST'])
@require_auth
def upload_document():
    """Upload a document file (multipart, 5 MB limit)."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '' or file.filename is None:
        return jsonify({'error': 'Empty filename'}), 400

    # MIME type detection
    safe_name = secure_filename(file.filename)
    if not safe_name:
        return jsonify({'error': 'Invalid filename'}), 400

    mime_type = file.content_type or mimetypes.guess_type(safe_name)[0] or 'application/octet-stream'
    if not _is_allowed_mime(mime_type):
        return jsonify({'error': f'File type not allowed: {mime_type}'}), 415

    # Size check (pre-save, from Content-Length header)
    if request.content_length and request.content_length > MAX_DOCUMENT_SIZE:
        return jsonify({'error': f'File too large (max {MAX_DOCUMENT_SIZE // (1024*1024)} MB)'}), 413

    doc_id = uuid.uuid4()
    doc_dir = _doc_storage_dir(tenant_id, user_id, str(doc_id))
    doc_dir.mkdir(parents=True, exist_ok=True)

    # Determine extension from original filename
    _, ext = os.path.splitext(safe_name)
    stored_name = f"original{ext}" if ext else "original"
    file_path = doc_dir / stored_name

    try:
        file.save(str(file_path))
    except Exception as e:
        current_app.logger.error("Document upload save failed: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to save file'}), 500

    # Post-save size validation (defense-in-depth)
    file_size = file_path.stat().st_size
    if file_size > MAX_DOCUMENT_SIZE:
        file_path.unlink(missing_ok=True)
        return jsonify({'error': f'File too large (max {MAX_DOCUMENT_SIZE // (1024*1024)} MB)'}), 413

    if file_size == 0:
        file_path.unlink(missing_ok=True)
        return jsonify({'error': 'Empty file'}), 400

    # Optional metadata from form fields
    title = request.form.get('title') or safe_name
    category = request.form.get('category')
    requested_folder_id = request.form.get('folder_id') or None

    # Determine OCR status — text PDFs don't need OCR (Phase 2 will refine)
    ocr_status = 'pending'
    text_content = None
    if mime_type == 'text/plain':
        ocr_status = 'not_needed'
        # Text uploads skip the OCR pipeline entirely, so ocr_text_full (FTS)
        # and the embedding are populated here instead of by the workers.
        try:
            text_content = file_path.read_text(encoding='utf-8', errors='replace')
        except OSError as e:
            current_app.logger.warning("text upload read failed for %s: %s", file_path, e)

    # Compute content hash on the saved bytes (integrity + dedup signal).
    try:
        sha256 = _sha256_file(file_path)
    except OSError as e:
        current_app.logger.error("SHA256 compute failed for %s: %s", file_path, e)
        file_path.unlink(missing_ok=True)
        return jsonify({'error': 'Failed to hash file'}), 500

    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Resolve destination folder. Caller-supplied folder_id must belong to
        # the user and be live; otherwise fall back to the system 'Documents' folder.
        folder_id = None
        if requested_folder_id:
            cur.execute("""
                SELECT id FROM document_folders
                WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
            """, (tenant_id, str(user_id), requested_folder_id))
            row = cur.fetchone()
            if not row:
                cur.close()
                conn.close()
                file_path.unlink(missing_ok=True)
                return jsonify({'error': 'Folder not found'}), 404
            folder_id = row['id']
        else:
            cur.execute("""
                SELECT id FROM document_folders
                WHERE tenant_id = %s AND user_id = %s
                  AND is_system = TRUE AND name = 'Documents'
                  AND deleted_at IS NULL
                LIMIT 1
            """, (tenant_id, str(user_id)))
            row = cur.fetchone()
            if not row:
                cur.close()
                conn.close()
                file_path.unlink(missing_ok=True)
                current_app.logger.error("Default Documents folder missing for user %s", user_id)
                return jsonify({'error': 'Default folder missing'}), 500
            folder_id = row['id']

        cur.execute("""
            INSERT INTO documents
                (tenant_id, id, user_id, folder_id, filename, mime_type, file_size_bytes,
                 file_path, sha256, source, ocr_status, ocr_text_full, title, category,
                 created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'upload', %s, %s, %s, %s, %s, %s)
            RETURNING id, folder_id, filename, mime_type, file_size_bytes, sha256,
                      source, ocr_status, quality_label, title, category, tags, created_at
        """, (
            tenant_id, str(doc_id), str(user_id), folder_id, safe_name, mime_type, file_size,
            str(file_path), sha256, ocr_status, text_content, title, category, now, now
        ))

        doc = cur.fetchone()
        conn.commit()

        if text_content:
            # Inline embedding (silent-fail) — same pattern as annotations.
            embed_field(conn, tenant_id, str(doc_id), 'documents',
                        'embedding_content', f"{title}\n\n{text_content}")

        cur.close()
        conn.close()

        if not doc:
            return jsonify({'error': 'Upload failed'}), 500

        doc['id'] = str(doc['id'])
        if doc.get('folder_id'):
            doc['folder_id'] = str(doc['folder_id'])
        if doc.get('created_at'):
            doc['created_at'] = doc['created_at'].isoformat()

        analytics.capture('document_uploaded', {'mime_type': mime_type})

        # OCR runs in-process (Home Edition — no broker). A background daemon
        # thread does render → Tesseract → page write → best-effort embed, so
        # the upload returns immediately and the client polls ocr_status until
        # 'complete' (unchanged contract from the old queue pipeline).
        if ocr_status == 'pending':
            from background import fire_and_forget
            from ocr import process_document_inline
            fire_and_forget(
                process_document_inline,
                tenant_id, str(user_id), str(doc_id), str(file_path),
            )

        current_app.logger.info("Document uploaded: id=%s filename=%s size=%d user=%s",
                                doc_id, safe_name, file_size, user_id)
        return jsonify(doc), 201

    except Exception as e:
        # Clean up file on DB failure
        file_path.unlink(missing_ok=True)
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/documents/upload', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("Document upload DB insert failed: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to save document record'}), 500


# ==================== CHAT SUMMARIES ====================

# Chat-session summaries are ordinary documents in the per-user
# 'AI Sessions' system folder: markdown file on disk, FTS via the
# ocr_text_full generated-tsvector path, inline embedding. Patient-authored
# PHI — every create writes an audit_log row (HIPAA §164.312(b)).
MAX_SUMMARY_CHARS = 256 * 1024
AI_SESSIONS_FOLDER = 'AI Sessions'


@bp.route('/documents/chat-summaries', methods=['POST'])
@require_auth
def create_chat_summary():
    """Persist an AI chat-session summary into the user's document collection.

    Body: title (required, ≤200 chars), summary_markdown (required, ≤256 KB),
    optional model_id, source_tools (list of tool names), session_started_at
    (ISO 8601). The document lands in the 'AI Sessions' system folder with
    source='chat_summary' and provenance recorded.
    """
    import json as _json

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    summary = data.get('summary_markdown') or ''
    if not title:
        return jsonify({'error': 'title is required'}), 400
    if len(title) > 200:
        return jsonify({'error': 'title must be 200 characters or fewer'}), 400
    if not summary.strip():
        return jsonify({'error': 'summary_markdown is required'}), 400
    if len(summary) > MAX_SUMMARY_CHARS:
        return jsonify({'error': f'summary_markdown must be {MAX_SUMMARY_CHARS} characters or fewer'}), 400

    model_id = data.get('model_id')
    source_tools = data.get('source_tools')
    if source_tools is not None and (
        not isinstance(source_tools, list)
        or not all(isinstance(t, str) for t in source_tools)
    ):
        return jsonify({'error': 'source_tools must be a list of strings'}), 400
    session_started_at = data.get('session_started_at')

    doc_id = uuid.uuid4()
    doc_dir = _doc_storage_dir(tenant_id, user_id, str(doc_id))
    doc_dir.mkdir(parents=True, exist_ok=True)
    file_path = doc_dir / 'original.md'
    try:
        file_path.write_text(summary, encoding='utf-8')
        sha256 = _sha256_file(file_path)
    except OSError as e:
        current_app.logger.error("Chat summary save failed: %s", e, exc_info=True)
        file_path.unlink(missing_ok=True)
        return jsonify({'error': 'Failed to save summary file'}), 500

    filename = (secure_filename(title) or 'chat-summary')[:120] + '.md'
    provenance = {
        'model_id': model_id,
        'source_tools': source_tools or [],
        'session_started_at': session_started_at,
        'created_via': data.get('created_via') or 'api',
    }
    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Resolve the AI Sessions system folder; self-heal for accounts that
        # predate the 2026-07-15 delta (INSERT runs under the user's own RLS
        # context, so WITH CHECK constrains it to the acting user).
        cur.execute("""
            SELECT id FROM document_folders
            WHERE tenant_id = %s AND user_id = %s
              AND is_system = TRUE AND name = %s AND deleted_at IS NULL
            LIMIT 1
        """, (tenant_id, str(user_id), AI_SESSIONS_FOLDER))
        row = cur.fetchone()
        if row:
            folder_id = row['id']
        else:
            cur.execute("""
                INSERT INTO document_folders (tenant_id, user_id, parent_id, name, is_system)
                VALUES (%s, %s, NULL, %s, TRUE)
                RETURNING id
            """, (tenant_id, str(user_id), AI_SESSIONS_FOLDER))
            folder_id = cur.fetchone()['id']

        cur.execute("""
            INSERT INTO documents
                (tenant_id, id, user_id, folder_id, filename, mime_type, file_size_bytes,
                 file_path, sha256, source, ocr_status, ocr_text_full, title, category,
                 provenance, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'text/markdown', %s, %s, %s, 'chat_summary',
                    'not_needed', %s, %s, 'ai_session', %s::jsonb, %s, %s)
            RETURNING id, folder_id, filename, mime_type, file_size_bytes, sha256,
                      source, ocr_status, title, category, created_at
        """, (
            tenant_id, str(doc_id), str(user_id), folder_id, filename,
            file_path.stat().st_size, str(file_path), sha256, summary, title,
            _json.dumps(provenance), now, now
        ))
        doc = cur.fetchone()

        # HIPAA §164.312(b): audit row for the patient-authored PHI write.
        cur.execute("""
            INSERT INTO audit_log (tenant_id, user_id, action, target_type, target_id, details, ip_address)
            VALUES (%s, %s, 'document.chat_summary_created', 'document', %s, %s::jsonb, %s)
        """, (
            tenant_id, str(user_id), str(doc_id),
            _json.dumps({'created_via': provenance['created_via'], 'model_id': model_id}),
            request.remote_addr,
        ))

        conn.commit()

        # Inline embedding (silent-fail, post-commit) — annotations pattern.
        embed_field(conn, tenant_id, str(doc_id), 'documents',
                    'embedding_content', f"{title}\n\n{summary}")

        cur.close()
        conn.close()

        doc['id'] = str(doc['id'])
        doc['folder_id'] = str(doc['folder_id'])
        doc['created_at'] = doc['created_at'].isoformat()
        doc['links'] = _doc_links(doc['id'])

        analytics.capture('chat_summary_saved', {'created_via': provenance['created_via']})
        current_app.logger.info("Chat summary saved: id=%s user=%s chars=%d",
                                doc_id, user_id, len(summary))
        return jsonify(doc), 201

    except Exception as e:
        file_path.unlink(missing_ok=True)
        if 'documents_source_check' in str(e):
            # Delta not applied yet — surface a clear signal, not a raw 500.
            current_app.logger.error("chat-summaries: schema not ready (source CHECK): %s", e)
            return jsonify({'error': 'Server schema not ready for chat summaries',
                            'code': 'SCHEMA_NOT_READY'}), 503
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/documents/chat-summaries', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("Chat summary create failed: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to save chat summary'}), 500


# 'Episode Reports' system folder: single-page HTML artifact on disk,
# searchable narrative text in ocr_text_full (drives fts + embedding),
# episode window/version/annotations in provenance JSONB.
# Plan: APIDocumentation/EpisodeReports-Plan1.md
MAX_REPORT_HTML_CHARS = 2 * 1024 * 1024
MAX_NARRATIVE_CHARS = 256 * 1024
EPISODE_REPORTS_FOLDER = 'Episode Reports'


def _parse_episode_instant(value):
    """App-convention timestamp parse → UTC ISO string, or None if invalid."""
    try:
        return local_to_utc(value).isoformat()
    except (ValueError, TypeError, AttributeError):
        return None


@bp.route('/documents/episode-reports', methods=['POST'])
@require_auth
def create_episode_report():
    """Persist an Episode Analysis report into the user's document collection.

    Body: title (required, ≤200), report_html (required, ≤2 MB — the
    self-contained single-page artifact), narrative_text (required, ≤256 KB —
    lead + narrative + observations + caveats as plain text; drives FTS and
    the embedding), episode_start / episode_end (required, ISO 8601, the
    unpadded analyzed window), version (int ≥1, default 1),
    supersedes_document_id (optional — prior version being replaced; must be
    the caller's own episode_report document), annotations (optional object:
    spans/events/caveats/discarded_readings), model_id, source_tools,
    created_via.

    Reports are immutable: re-analysis creates a new document that supersedes
    the old one. The write path only touches the caller's own collection
    (explicit tenant_id/user_id on every statement — no RLS on this box);
    audit row per create (action='document.episode_report_created').
    """
    import json as _json

    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    report_html = data.get('report_html') or ''
    narrative = data.get('narrative_text') or ''
    if not title:
        return jsonify({'error': 'title is required'}), 400
    if len(title) > 200:
        return jsonify({'error': 'title must be 200 characters or fewer'}), 400
    if not report_html.strip():
        return jsonify({'error': 'report_html is required'}), 400
    if len(report_html) > MAX_REPORT_HTML_CHARS:
        return jsonify({'error': f'report_html must be {MAX_REPORT_HTML_CHARS} characters or fewer'}), 400
    if not narrative.strip():
        return jsonify({'error': 'narrative_text is required'}), 400
    if len(narrative) > MAX_NARRATIVE_CHARS:
        return jsonify({'error': f'narrative_text must be {MAX_NARRATIVE_CHARS} characters or fewer'}), 400

    episode_start = _parse_episode_instant(data.get('episode_start'))
    episode_end = _parse_episode_instant(data.get('episode_end'))
    if not episode_start or not episode_end:
        return jsonify({'error': 'episode_start and episode_end (ISO 8601) are required'}), 400
    if episode_end <= episode_start:
        return jsonify({'error': 'episode_end must be after episode_start'}), 400

    version = data.get('version', 1)
    if not isinstance(version, int) or version < 1:
        return jsonify({'error': 'version must be a positive integer'}), 400

    supersedes = data.get('supersedes_document_id')
    if supersedes is not None:
        try:
            supersedes = str(uuid.UUID(str(supersedes)))
        except ValueError:
            return jsonify({'error': 'supersedes_document_id must be a UUID'}), 400

    annotations = data.get('annotations')
    if annotations is not None and not isinstance(annotations, dict):
        return jsonify({'error': 'annotations must be an object'}), 400

    source_tools = data.get('source_tools')
    if source_tools is not None and (
        not isinstance(source_tools, list)
        or not all(isinstance(t, str) for t in source_tools)
    ):
        return jsonify({'error': 'source_tools must be a list of strings'}), 400

    doc_id = uuid.uuid4()
    doc_dir = _doc_storage_dir(tenant_id, user_id, str(doc_id))
    doc_dir.mkdir(parents=True, exist_ok=True)
    file_path = doc_dir / 'original.html'
    try:
        file_path.write_text(report_html, encoding='utf-8')
        sha256 = _sha256_file(file_path)
    except OSError as e:
        current_app.logger.error("Episode report save failed: %s", e, exc_info=True)
        file_path.unlink(missing_ok=True)
        return jsonify({'error': 'Failed to save report file'}), 500

    filename = (secure_filename(title) or 'episode-report')[:120] + '.html'
    provenance = {
        'episode_start': episode_start,
        'episode_end': episode_end,
        'version': version,
        'supersedes_document_id': supersedes,
        'annotations': annotations or {},
        'model_id': data.get('model_id'),
        'source_tools': source_tools or [],
        'created_via': data.get('created_via') or 'api',
    }
    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Supersedes must reference the caller's own live episode report
        # (explicit user_id scoping — no RLS on this box).
        if supersedes:
            cur.execute("""
                SELECT 1 FROM documents
                WHERE tenant_id = %s AND user_id = %s AND id = %s
                  AND source = 'episode_report' AND deleted_at IS NULL
            """, (tenant_id, str(user_id), supersedes))
            if not cur.fetchone():
                cur.close()
                conn.close()
                file_path.unlink(missing_ok=True)
                return jsonify({'error': 'supersedes_document_id does not reference one of your episode reports'}), 400

        # Resolve the Episode Reports system folder; self-heal for accounts
        # that predate the 2026-07-20 delta.
        cur.execute("""
            SELECT id FROM document_folders
            WHERE tenant_id = %s AND user_id = %s
              AND is_system = TRUE AND name = %s AND deleted_at IS NULL
            LIMIT 1
        """, (tenant_id, str(user_id), EPISODE_REPORTS_FOLDER))
        row = cur.fetchone()
        if row:
            folder_id = row['id']
        else:
            cur.execute("""
                INSERT INTO document_folders (tenant_id, user_id, parent_id, name, is_system)
                VALUES (%s, %s, NULL, %s, TRUE)
                RETURNING id
            """, (tenant_id, str(user_id), EPISODE_REPORTS_FOLDER))
            folder_id = cur.fetchone()['id']

        cur.execute("""
            INSERT INTO documents
                (tenant_id, id, user_id, folder_id, filename, mime_type, file_size_bytes,
                 file_path, sha256, source, ocr_status, ocr_text_full, title, category,
                 provenance, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'text/html', %s, %s, %s, 'episode_report',
                    'not_needed', %s, %s, 'episode_report', %s::jsonb, %s, %s)
            RETURNING id, folder_id, filename, mime_type, file_size_bytes, sha256,
                      source, ocr_status, title, category, created_at
        """, (
            tenant_id, str(doc_id), str(user_id), folder_id, filename,
            file_path.stat().st_size, str(file_path), sha256, narrative, title,
            _json.dumps(provenance), now, now
        ))
        doc = cur.fetchone()

        # HIPAA §164.312(b): audit row for the patient-authored PHI write.
        cur.execute("""
            INSERT INTO audit_log (tenant_id, user_id, action, target_type, target_id, details, ip_address)
            VALUES (%s, %s, 'document.episode_report_created', 'document', %s, %s::jsonb, %s)
        """, (
            tenant_id, str(user_id), str(doc_id),
            _json.dumps({'created_via': provenance['created_via'],
                         'model_id': provenance['model_id'],
                         'episode_start': episode_start,
                         'episode_end': episode_end,
                         'version': version}),
            request.remote_addr,
        ))

        conn.commit()

        # Inline embedding (silent-fail, post-commit) — chat-summary pattern.
        embed_field(conn, tenant_id, str(doc_id), 'documents',
                    'embedding_content', f"{title}\n\n{narrative}")

        cur.close()
        conn.close()

        doc['id'] = str(doc['id'])
        doc['folder_id'] = str(doc['folder_id'])
        doc['created_at'] = doc['created_at'].isoformat()
        doc['episode_start'] = episode_start
        doc['episode_end'] = episode_end
        doc['version'] = version
        doc['links'] = _doc_links(doc['id'])

        analytics.capture('episode_report_saved', {'created_via': provenance['created_via'], 'version': version})
        current_app.logger.info("Episode report saved: id=%s user=%s window=%s..%s v%d",
                                doc_id, user_id, episode_start, episode_end, version)
        return jsonify(doc), 201

    except Exception as e:
        file_path.unlink(missing_ok=True)
        if 'documents_source_check' in str(e):
            current_app.logger.error("episode-reports: schema not ready (source CHECK): %s", e)
            return jsonify({'error': 'Server schema not ready for episode reports',
                            'code': 'SCHEMA_NOT_READY'}), 503
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/documents/episode-reports', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("Episode report create failed: %s", e, exc_info=True)
        return jsonify({'error': 'Failed to save episode report'}), 500


@bp.route('/documents/episode-reports', methods=['GET'])
@require_auth
def list_episode_reports():
    """List the user's episode reports — envelope metadata only, never HTML.

    Query params: from / to (ISO 8601; returns reports whose analyzed window
    OVERLAPS [from, to]), latest_only (default true — hide reports superseded
    by a newer live version), limit / offset.

    Minimum-Necessary (§164.502(b)): envelope only; the report body travels
    only on explicit single-document fetch/view/download.
    """
    limit, offset = parse_pagination_params()
    latest_only = (request.args.get('latest_only') or 'true').lower() != 'false'

    from_raw = request.args.get('from')
    to_raw = request.args.get('to')
    from_utc = _parse_episode_instant(from_raw) if from_raw else None
    to_utc = _parse_episode_instant(to_raw) if to_raw else None
    if (from_raw and not from_utc) or (to_raw and not to_utc):
        return jsonify({'error': 'Invalid from/to timestamp; use ISO 8601'}), 400

    conditions = [sql.SQL("d.tenant_id = %s"), sql.SQL("d.user_id = %s"),
                  sql.SQL("d.source = 'episode_report'"), sql.SQL("d.deleted_at IS NULL")]
    params: list = [g.user.get('tenant_id', 1), str(get_user_id())]
    # ISO-8601 UTC strings compare correctly as text — overlap test on the
    # provenance window without dedicated columns.
    if to_utc:
        conditions.append(sql.SQL("d.provenance->>'episode_start' < %s"))
        params.append(to_utc)
    if from_utc:
        conditions.append(sql.SQL("d.provenance->>'episode_end' > %s"))
        params.append(from_utc)
    if latest_only:
        conditions.append(sql.SQL("""NOT EXISTS (
            SELECT 1 FROM documents d2
            WHERE d2.tenant_id = d.tenant_id AND d2.user_id = d.user_id
              AND d2.source = 'episode_report' AND d2.deleted_at IS NULL
              AND d2.provenance->>'supersedes_document_id' = d.id::text
        )"""))

    query = sql.SQL("""
        SELECT count(*) OVER() AS _total,
               d.id, d.title, d.created_at,
               d.provenance->>'episode_start' AS episode_start,
               d.provenance->>'episode_end' AS episode_end,
               (d.provenance->>'version')::int AS version,
               d.provenance->>'supersedes_document_id' AS supersedes_document_id
        FROM documents d
        WHERE {conditions}
        ORDER BY d.provenance->>'episode_start' DESC
        LIMIT %s OFFSET %s
    """).format(conditions=sql.SQL(" AND ").join(conditions))

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(query, params + [limit, offset])
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/documents/episode-reports', str(get_user_id()))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("GET /documents/episode-reports failed: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500

    total = rows[0]['_total'] if rows else 0
    for r in rows:
        r.pop('_total', None)
        r['id'] = str(r['id'])
        r['created_at'] = r['created_at'].isoformat()
        r['links'] = _doc_links(r['id'])

    return jsonify(paginated_response(rows, total, limit, offset, key='reports'))


@bp.route('/documents/<doc_id>/view', methods=['GET'])
@require_auth
def view_document(doc_id):
    """Serve the document inline (browser-rendered), local storage only.

    text/html documents (episode reports) are LLM-session-generated user
    content — so HTML is served with a sandboxing CSP (opaque origin:
    scripts such as Chart.js run, but no cookies and no same-origin reads).
    Without it this is a stored-XSS path from generated content into the
    viewer's browser session. Remote-tier documents fall back to the
    download route's presign flow.
    """
    tenant_id = g.user.get('tenant_id', 1)

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT file_path, filename, mime_type, storage_tier
            FROM documents
            WHERE tenant_id = %s AND user_id = %s AND id = %s
              AND deleted_at IS NULL
        """, (tenant_id, str(get_user_id()), doc_id,))
        doc = cur.fetchone()
        cur.close()
        conn.close()

        if not doc:
            return jsonify({'error': 'Document not found'}), 404

        if doc.get('storage_tier', 'local') not in ('local', 'both') or not doc['file_path']:
            return redirect(f'/api/v1/documents/{doc_id}/download', code=302)
        file_path = Path(doc['file_path'])
        if not file_path.exists():
            return jsonify({'error': 'Document file not available'}), 404

        resp = send_file(
            str(file_path),
            mimetype=doc['mime_type'],
            as_attachment=False,
            download_name=doc['filename'],
        )
        if (doc['mime_type'] or '').startswith('text/html'):
            resp.headers['Content-Security-Policy'] = 'sandbox allow-scripts'
            resp.headers['X-Content-Type-Options'] = 'nosniff'
        return resp

    except Exception as e:
        current_app.logger.error("GET /documents/%s/view failed: %s", doc_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== LIST ====================

@bp.route('/documents', methods=['GET'])
@require_auth
def list_documents():
    """List a paginated page of the user's documents (excludes soft-deleted).

    Optional ?folder_id=<uuid> filter scopes the listing to one folder.
    Updated 2026-04-11 to match the spec's reference pagination shape:
    single-query count(*) OVER() instead of a separate COUNT query, and
    the nested {documents, pagination: {...}} envelope. This is a breaking
    change for any client reading response['total'] directly — none known
    at retrofit time.
    """
    user_id = get_user_id()
    limit, offset = parse_pagination_params()
    folder_filter = request.args.get('folder_id')

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if folder_filter:
            cur.execute("""
                SELECT count(*) OVER() AS _total,
                       id, folder_id, filename, mime_type, file_size_bytes, sha256,
                       source, ocr_status, quality_label, page_count, title,
                       category, tags, storage_tier, created_at, updated_at
                FROM documents
                WHERE user_id = %s AND deleted_at IS NULL AND folder_id = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (user_id, folder_filter, limit, offset))
        else:
            cur.execute("""
                SELECT count(*) OVER() AS _total,
                       id, folder_id, filename, mime_type, file_size_bytes, sha256,
                       source, ocr_status, quality_label, page_count, title,
                       category, tags, storage_tier, created_at, updated_at
                FROM documents
                WHERE user_id = %s AND deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (user_id, limit, offset))

        docs = cur.fetchall()
        cur.close()
        conn.close()

        total = docs[0]['_total'] if docs else 0
        for doc in docs:
            doc.pop('_total', None)
            doc['id'] = str(doc['id'])
            if doc.get('folder_id'):
                doc['folder_id'] = str(doc['folder_id'])
            if doc.get('created_at'):
                doc['created_at'] = doc['created_at'].isoformat()
            if doc.get('updated_at'):
                doc['updated_at'] = doc['updated_at'].isoformat()

        return jsonify(paginated_response(docs, total, limit, offset, key='entries'))

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill('/documents', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("GET /documents failed: %s", e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== DETAIL ====================

@bp.route('/documents/<doc_id>', methods=['GET'])
@require_auth
def get_document(doc_id):
    """Get document detail including page list."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, folder_id, filename, mime_type, file_size_bytes, sha256,
                   source, ocr_status, quality_label, page_count, title, category,
                   tags, ocr_text_full, storage_tier, created_at, updated_at
            FROM documents
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
        """, (tenant_id, user_id, doc_id,))

        doc = cur.fetchone()
        if not doc:
            cur.close()
            conn.close()
            return jsonify({'error': 'Document not found'}), 404

        # Fetch pages if any exist
        cur.execute("""
            SELECT id, page_number, ocr_text, ocr_confidence, quality_label, image_path
            FROM document_pages
            WHERE document_id = %s AND user_id = %s
            ORDER BY page_number
        """, (doc_id, user_id))

        pages = cur.fetchall()
        cur.close()
        conn.close()

        doc['id'] = str(doc['id'])
        if doc.get('folder_id'):
            doc['folder_id'] = str(doc['folder_id'])
        if doc.get('created_at'):
            doc['created_at'] = doc['created_at'].isoformat()
        if doc.get('updated_at'):
            doc['updated_at'] = doc['updated_at'].isoformat()

        for page in pages:
            page['id'] = str(page['id'])

        doc['pages'] = pages
        doc['links'] = _doc_links(doc['id'])
        return jsonify(doc)

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/documents/{doc_id}', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("GET /documents/%s failed: %s", doc_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== DOWNLOAD ====================

@bp.route('/documents/<doc_id>/download', methods=['GET'])
@require_auth
def download_document(doc_id):
    """Download the original document file.

    Serves from local disk when available. Falls back to a presigned URL
    redirect (302) for remote-only documents. Supports ?proxy=1 for
    clients that can't follow redirects.
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT file_path, filename, mime_type, sha256,
                   storage_tier, remote_key
            FROM documents
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
        """, (tenant_id, user_id, doc_id,))

        doc = cur.fetchone()
        cur.close()
        conn.close()

        if not doc:
            return jsonify({'error': 'Document not found'}), 404

        storage_tier = doc.get('storage_tier', 'local')

        # Local copy available — serve directly (fastest path)
        if storage_tier in ('local', 'both') and doc['file_path']:
            file_path = Path(doc['file_path'])
            if file_path.exists():
                # Integrity check: recompute and compare to stored sha256.
                # Mismatch is logged but does not block the download (the
                # file on disk is still what the user asked for) — surfacing
                # this via metrics/alerting is Phase 3 work.
                stored_sha = doc.get('sha256')
                if stored_sha:
                    try:
                        actual_sha = _sha256_file(file_path)
                        if actual_sha != stored_sha:
                            current_app.logger.error(
                                "SHA256 mismatch on download: doc=%s user=%s stored=%s actual=%s",
                                doc_id, user_id, stored_sha, actual_sha
                            )
                    except OSError as e:
                        current_app.logger.warning(
                            "SHA256 verify skipped (IO error) doc=%s: %s", doc_id, e
                        )
                return send_file(
                    str(file_path),
                    mimetype=doc['mime_type'],
                    as_attachment=True,
                    download_name=doc['filename']
                )

        # Remote only — presigned URL redirect or proxy
        if storage_tier in ('remote', 'both') and doc.get('remote_key'):
            from object_store import get_object_store
            store = get_object_store()

            # Proxy mode: stream through Flask (read into memory first
            # to avoid temp file race with WSGI streaming)
            if request.args.get('proxy') == '1':
                import io
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    store.get(doc['remote_key'], tmp_path)
                    with open(tmp_path, 'rb') as f:
                        data = io.BytesIO(f.read())
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
                return send_file(
                    data,
                    mimetype=doc['mime_type'],
                    as_attachment=True,
                    download_name=doc['filename']
                )

            # Default: presigned URL redirect
            presigned = store.presign(doc['remote_key'], expires_seconds=300)
            return redirect(presigned.url, code=302)

        current_app.logger.error("Document file unavailable: doc=%s tier=%s", doc_id, storage_tier)
        return jsonify({'error': 'Document file not available'}), 404

    except Exception as e:
        current_app.logger.error("GET /documents/%s/download failed: %s", doc_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== PAGE IMAGE (THUMBNAIL/VIEWER) ====================

@bp.route('/documents/<doc_id>/pages/<int:page_number>/image', methods=['GET'])
@require_auth
def get_page_image(doc_id, page_number):
    """Serve a rendered page PNG inline. Used by the documents UI for thumbnails
    and any future per-page viewer. The query filters document_pages by tenant_id + user_id.
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.image_path
            FROM document_pages p
            JOIN documents d
              ON d.tenant_id = p.tenant_id AND d.id = p.document_id
            WHERE p.tenant_id = %s
              AND p.user_id = %s
              AND p.document_id = %s
              AND p.page_number = %s
              AND d.deleted_at IS NULL
        """, (tenant_id, user_id, doc_id, page_number))
        row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(
                f'/documents/{doc_id}/pages/{page_number}/image', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("GET /documents/%s/pages/%s/image failed: %s",
                                 doc_id, page_number, e, exc_info=True)
        return jsonify({'error': str(e)}), 500

    if not row or not row.get('image_path'):
        return jsonify({'error': 'Page image not available'}), 404

    image_path = Path(row['image_path']).resolve()
    # Defense-in-depth: refuse to serve anything outside the userdocs root.
    try:
        image_path.relative_to(STORAGE_ROOT.resolve())
    except ValueError:
        current_app.logger.error("Page image escapes STORAGE_ROOT: doc=%s path=%s",
                                 doc_id, image_path)
        return jsonify({'error': 'Page image not available'}), 404

    if not image_path.exists():
        return jsonify({'error': 'Page image file missing'}), 404

    response = send_file(
        str(image_path),
        mimetype='image/png',
        as_attachment=False,
        download_name=f'page_{page_number:03d}.png',
    )
    response.headers['Cache-Control'] = 'private, max-age=3600'
    return response


# ==================== UPDATE METADATA ====================

@bp.route('/documents/<doc_id>', methods=['PATCH'])
@require_auth
def update_document(doc_id):
    """Update document metadata (title, category, tags, filename, folder_id).

    `filename` renames the user-visible name — the on-disk path is unchanged.
    `folder_id` moves the document; the target folder must belong to the user
    and be live. Moves do not rewrite the remote key (that's keyed by document_id).
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    data = request.json

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    allowed_fields = {'title', 'category', 'tags', 'filename', 'folder_id'}
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if not updates:
        return jsonify({'error': 'No valid fields to update'}), 400

    # Normalize filename
    if 'filename' in updates:
        fn = updates['filename']
        if not isinstance(fn, str) or not fn.strip() or len(fn) > 255:
            return jsonify({'error': 'Invalid filename'}), 400
        if any(c in fn for c in ('/', '\\', '\x00')):
            return jsonify({'error': 'Filename cannot contain path separators'}), 400
        updates['filename'] = fn.strip()

    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Validate folder_id if moving
        if 'folder_id' in updates:
            target_folder = updates['folder_id']
            if not target_folder:
                cur.close()
                conn.close()
                return jsonify({'error': 'folder_id cannot be empty'}), 400
            cur.execute("""
                SELECT id FROM document_folders
                WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
            """, (tenant_id, str(user_id), target_folder))
            if not cur.fetchone():
                cur.close()
                conn.close()
                return jsonify({'error': 'Target folder not found'}), 404

        # Build dynamic SET clause
        set_parts = []
        params = []
        for field, value in updates.items():
            set_parts.append(sql.SQL("{} = %s").format(sql.Identifier(field)))
            params.append(value if field != 'tags' else (value if isinstance(value, list) else value))

        set_parts.append(sql.SQL("updated_at = %s"))
        params.append(now)
        params.append(tenant_id)
        params.append(user_id)
        params.append(doc_id)

        update_query = sql.SQL("""
            UPDATE documents
            SET {set_clause}
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
            RETURNING id, folder_id, filename, title, category, tags, updated_at
        """).format(set_clause=sql.SQL(", ").join(set_parts))
        cur.execute(update_query, params)

        doc = cur.fetchone()
        if not doc:
            cur.close()
            conn.close()
            return jsonify({'error': 'Document not found'}), 404

        conn.commit()
        cur.close()
        conn.close()

        doc['id'] = str(doc['id'])
        if doc.get('folder_id'):
            doc['folder_id'] = str(doc['folder_id'])
        if doc.get('updated_at'):
            doc['updated_at'] = doc['updated_at'].isoformat()

        return jsonify(doc)

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/documents/{doc_id}', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("PATCH /documents/%s failed: %s", doc_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== SOFT DELETE ====================

@bp.route('/documents/<doc_id>', methods=['DELETE'])
@require_auth
def delete_document(doc_id):
    """Soft-delete a document (sets deleted_at, preserves file)."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE documents
            SET deleted_at = %s, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
            RETURNING id
        """, (now, now, tenant_id, user_id, doc_id))

        doc = cur.fetchone()
        if not doc:
            cur.close()
            conn.close()
            return jsonify({'error': 'Document not found'}), 404

        conn.commit()
        cur.close()
        conn.close()

        current_app.logger.info("Document soft-deleted: id=%s user=%s", doc_id, user_id)
        return jsonify({'deleted': True, 'id': str(doc['id'])}), 200

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/documents/{doc_id}', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("DELETE /documents/%s failed: %s", doc_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== RESTORE ====================

@bp.route('/documents/<doc_id>/restore', methods=['POST'])
@require_auth
def restore_document(doc_id):
    """Restore a soft-deleted document. Requires its folder to be live."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT d.id, d.folder_id, f.deleted_at AS folder_deleted_at
            FROM documents d
            JOIN document_folders f
              ON f.tenant_id = d.tenant_id AND f.id = d.folder_id
            WHERE d.tenant_id = %s AND d.user_id = %s AND d.id = %s AND d.deleted_at IS NOT NULL
        """, (tenant_id, user_id, doc_id))
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return jsonify({'error': 'Trashed document not found'}), 404
        if row['folder_deleted_at'] is not None:
            cur.close()
            conn.close()
            return jsonify({'error': 'Parent folder is not available; restore it first'}), 409

        cur.execute("""
            UPDATE documents
            SET deleted_at = NULL, updated_at = %s
            WHERE tenant_id = %s AND user_id = %s AND id = %s
            RETURNING id
        """, (now, tenant_id, user_id, doc_id))
        conn.commit()
        cur.close()
        conn.close()

        current_app.logger.info("Document restored: id=%s user=%s", doc_id, user_id)
        return jsonify({'restored': True, 'id': str(doc_id)})

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/documents/{doc_id}/restore', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("POST /documents/%s/restore failed: %s", doc_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== ANNOTATIONS: CREATE ====================

@bp.route('/documents/<doc_id>/annotations', methods=['POST'])
@require_auth
def create_annotation(doc_id):
    """Create an annotation on a document (page-level or document-level).

    Requires the document to be owned by the current user (checked by the SQL predicate).
    Sets author_type='user' and author_id to current user.
    """
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    data = request.json

    if not data or not data.get('body', '').strip():
        return jsonify({'error': 'body is required'}), 400

    body = data['body'].strip()
    page_number = data.get('page_number')  # None = document-level

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verify the document exists and belongs to the current user (explicit user_id)
        cur.execute("""
            SELECT id, user_id FROM documents
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND deleted_at IS NULL
        """, (tenant_id, user_id, doc_id,))

        doc = cur.fetchone()
        if not doc:
            cur.close()
            conn.close()
            return jsonify({'error': 'Document not found'}), 404

        doc_owner_id = str(doc['user_id'])
        now = datetime.now(pytz.utc)

        cur.execute("""
            INSERT INTO document_annotations
                (tenant_id, user_id, document_id, author_type, author_id,
                 page_number, body, created_at, updated_at)
            VALUES (%s, %s, %s, 'user', %s, %s, %s, %s, %s)
            RETURNING id, document_id, author_type, author_id,
                      page_number, body, created_at, updated_at
        """, (tenant_id, doc_owner_id, doc_id, str(user_id),
              page_number, body, now, now))

        ann = cur.fetchone()
        conn.commit()
        cur.close()
        assert ann is not None

        embed_field(
            conn, tenant_id, ann['id'],
            'document_annotations', 'embedding_body',
            body, data.get('embedding'),
        )

        conn.close()

        ann['id'] = str(ann['id'])
        ann['document_id'] = str(ann['document_id'])
        ann['author_id'] = str(ann['author_id'])
        if ann.get('created_at'):
            ann['created_at'] = ann['created_at'].isoformat()
        if ann.get('updated_at'):
            ann['updated_at'] = ann['updated_at'].isoformat()

        current_app.logger.info("Annotation created: id=%s doc=%s author=%s",
                                ann['id'], doc_id, user_id)
        return jsonify(ann), 201

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/documents/{doc_id}/annotations', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("POST /documents/%s/annotations failed: %s", doc_id, e, exc_info=True)
        return jsonify({'error': 'Failed to create annotation'}), 500


# ==================== ANNOTATIONS: LIST ====================

@bp.route('/documents/<doc_id>/annotations', methods=['GET'])
@require_auth
def list_annotations(doc_id):
    """List annotations on a document (paginated).

    Scoped to the owning user via the explicit user_id predicate.
    Joins to users to include the author's display name.
    """
    user_id = get_user_id()
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT a.id, a.document_id, a.author_type, a.author_id,
                   a.page_number, a.body, a.created_at, a.updated_at,
                   COALESCE(u.display_name, 'Unknown') as author_name
            FROM document_annotations a
            LEFT JOIN users u ON u.tenant_id = a.tenant_id AND u.id = a.author_id
            WHERE a.document_id = %s AND a.user_id = %s
            ORDER BY a.created_at DESC
            LIMIT %s OFFSET %s
        """, (doc_id, str(user_id), limit, offset))

        annotations = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*) as total FROM document_annotations
            WHERE document_id = %s AND user_id = %s
        """, (doc_id, str(user_id)))
        total = cur.fetchone()['total']

        cur.close()
        conn.close()

        for ann in annotations:
            ann['id'] = str(ann['id'])
            ann['document_id'] = str(ann['document_id'])
            ann['author_id'] = str(ann['author_id'])
            if ann.get('created_at'):
                ann['created_at'] = ann['created_at'].isoformat()
            if ann.get('updated_at'):
                ann['updated_at'] = ann['updated_at'].isoformat()

        return jsonify({
            'annotations': annotations,
            'total': total,
            'limit': limit,
            'offset': offset,
        })

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/documents/{doc_id}/annotations', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("GET /documents/%s/annotations failed: %s", doc_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== ANNOTATIONS: UPDATE ====================

@bp.route('/documents/<doc_id>/annotations/<ann_id>', methods=['PATCH'])
@require_auth
def update_annotation(doc_id, ann_id):
    """Update an annotation's body. Only the original author can edit."""
    user_id = get_user_id()
    tenant_id = g.user.get('tenant_id', 1)
    data = request.json

    if not data or not data.get('body', '').strip():
        return jsonify({'error': 'body is required'}), 400

    body = data['body'].strip()
    now = datetime.now(pytz.utc)

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Update only if user is the author
        cur.execute("""
            UPDATE document_annotations
            SET body = %s, updated_at = %s
            WHERE id = %s AND document_id = %s AND user_id = %s AND author_id = %s
            RETURNING id, body, updated_at
        """, (body, now, ann_id, doc_id, str(user_id), str(user_id)))

        ann = cur.fetchone()
        if not ann:
            cur.close()
            conn.close()
            return jsonify({'error': 'Annotation not found or not owned by you'}), 404

        conn.commit()
        cur.close()

        embed_field(
            conn, tenant_id, ann['id'],  # ann is not None — RETURNING guaranteed a row
            'document_annotations', 'embedding_body',
            body, data.get('embedding'),
        )

        conn.close()

        ann['id'] = str(ann['id'])
        if ann.get('updated_at'):
            ann['updated_at'] = ann['updated_at'].isoformat()

        return jsonify(ann)

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/documents/{doc_id}/annotations/{ann_id}', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("PATCH /documents/%s/annotations/%s failed: %s",
                                 doc_id, ann_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500


# ==================== ANNOTATIONS: DELETE ====================

@bp.route('/documents/<doc_id>/annotations/<ann_id>', methods=['DELETE'])
@require_auth
def delete_annotation(doc_id, ann_id):
    """Delete an annotation. Author can delete own. Document owner can delete any."""
    user_id = get_user_id()

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Fetch annotation to check ownership
        tenant_id = g.user.get('tenant_id', 1)
        cur.execute("""
            SELECT id, author_id, user_id FROM document_annotations
            WHERE tenant_id = %s AND user_id = %s AND id = %s AND document_id = %s
        """, (tenant_id, str(user_id), ann_id, doc_id))

        ann = cur.fetchone()
        if not ann:
            cur.close()
            conn.close()
            return jsonify({'error': 'Annotation not found'}), 404

        # Allow delete if: author, or document owner (moderator)
        is_author = str(ann['author_id']) == str(user_id)
        is_doc_owner = str(ann['user_id']) == str(user_id)

        if not is_author and not is_doc_owner:
            cur.close()
            conn.close()
            return jsonify({'error': 'Only annotation author or document owner can delete'}), 403

        cur.execute("""
            DELETE FROM document_annotations WHERE id = %s AND document_id = %s AND user_id = %s
        """, (ann_id, doc_id, str(user_id)))

        conn.commit()
        cur.close()
        conn.close()

        current_app.logger.info("Annotation deleted: id=%s doc=%s by=%s", ann_id, doc_id, user_id)
        return jsonify({'deleted': True, 'id': str(ann['id'])})

    except Exception as e:
        if db_manager.is_query_killed(e):
            db_manager.log_and_count_query_kill(f'/documents/{doc_id}/annotations/{ann_id}', str(user_id))
            return jsonify({'error': 'Query timeout', 'code': 'QUERY_TIMEOUT'}), 503
        current_app.logger.error("DELETE /documents/%s/annotations/%s failed: %s",
                                 doc_id, ann_id, e, exc_info=True)
        return jsonify({'error': str(e)}), 500

"""Live test: /api/v1/documents upload → OCR → annotation CRUD → download → soft-delete.

This is the first automated end-to-end coverage of the UserDocs pipeline.
Complements the hand-curl-driven UserDocs/TestPlan-Pipeline.md with a
repeatable, delta-asserting flow suitable for `run_all`.

Scope (what this flow does assert):
- Upload path: POST /api/v1/documents/upload with a multipart PDF,
  assert 201 and that `documents` row count incremented by exactly one
  under RLS context for the test user.
- OCR path: poll GET /api/v1/documents/{id} until `ocr_status` leaves
  'pending'. Accept 'complete' (happy path) or 'failed' (pipeline ran
  but couldn't extract text); hard-fail only on timeout, since a timeout
  means the OCR worker isn't consuming or the queue is stuck.
- Download path (local tier): GET /api/v1/documents/{id}/download →
  200 with byte-for-byte match to the upload. This covers the
  `storage_tier in ('local','both')` branch of download_document.
- Annotation CRUD: POST/GET/PATCH/DELETE on /annotations with
  document_annotations delta assertions.
- Soft delete: DELETE /api/v1/documents/{id} → the doc falls off
  the non-deleted list. `deleted_at IS NOT NULL` confirmed via DB.

Out of scope (for now):
- Presigned/R2 download path. That requires storage_tier='both',
  which requires the embed step to run — embed needs Ollama on a
  private network. prodvps (public) can't reach Ollama today, so
  the stash step won't fire there. A separate flow or an env-guarded
  step can add that coverage once the embed link is solved.
- Fax intake → OCR. Covered separately when a fax flow is written.

Cleanup: uses the `title` column with the `livetest-<uuid>` prefix so
livetest/cleanup.py can hard-DELETE survivors from aborted runs.
`document_pages` and `document_annotations` cascade on documents.id,
so no explicit child cleanup is needed.
"""
from __future__ import annotations

import hashlib
import io
import sys
import time
import uuid

from livetest.auth import login
from livetest.config import load_config
from livetest.pagination_assertions import assert_pagination_envelope
from livetest.pg import count_rows, open_rls_connection
from livetest.report import print_live, write_markdown
from livetest.runner import Flow, FlowResult

# OCR poll budget. The happy path on prodvps is ~10-20s end-to-end for
# a 1-page PDF; 120s is well above that but still short enough that a
# stuck worker surfaces as a test failure rather than a hung run.
OCR_POLL_TIMEOUT_SEC = 120
OCR_POLL_INTERVAL_SEC = 2


def _build_sample_pdf(title_text: str) -> bytes:
    """Build a minimal valid PDF with one text line for OCR.

    Hand-assembled to avoid a reportlab / fpdf dependency just for
    livetest. Renders as a single letter-size page with the given
    text in 36pt Helvetica — large enough for Tesseract to read
    reliably. Output is ~500 bytes.
    """
    content = (
        b"BT /F1 36 Tf 72 720 Td (" + title_text.encode("ascii") + b") Tj ET"
    )
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Count 1/Kids[3 0 R]>>",
        (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>"
        ),
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n"
        + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xff\xff\xff\xff\n")
    offsets: list[int] = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref_offset = len(out)
    n_objs = len(objs) + 1  # include the free entry
    out += b"xref\n0 " + str(n_objs).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += b"trailer\n<</Size " + str(n_objs).encode() + b"/Root 1 0 R>>\n"
    out += b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    return bytes(out)


class DocumentsFlow(Flow):
    name = "documents"

    def run(self) -> FlowResult:
        cur = self.conn.cursor()
        run_tag = f"livetest-{uuid.uuid4().hex[:8]}"
        filename = f"{run_tag}.pdf"
        title = run_tag  # used by cleanup.py name-prefix filter

        pdf_bytes = _build_sample_pdf(run_tag.upper())
        upload_md5 = hashlib.md5(pdf_bytes, usedforsecurity=False).hexdigest()
        upload_sha256 = hashlib.sha256(pdf_bytes).hexdigest()

        doc_id: str | None = None
        ann_id: str | None = None

        # Phase 2: folder setup — system folders seed on user INSERT, so we
        # assert they're present, then create a user folder to upload into.
        docs_folder_id: str | None = None
        fax_folder_id: str | None = None
        user_folder_id: str | None = None
        user_folder_name = f"folder-{run_tag}"

        with self.step("GET /api/v1/folders lists the two system folders"):
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/folders",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            folders = resp.json().get("folders", [])
            systems = {f["name"]: f for f in folders if f.get("is_system")}
            assert "Documents" in systems, f"system folder Documents missing: {folders}"
            assert "Fax" in systems, f"system folder Fax missing: {folders}"
            docs_folder_id = systems["Documents"]["id"]
            fax_folder_id = systems["Fax"]["id"]

        with self.step("POST /api/v1/folders creates a user folder"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/folders",
                json={"name": user_folder_name},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert body.get("name") == user_folder_name, (
                f"folder name mismatch: got {body.get('name')!r}"
            )
            assert body.get("is_system") is False, (
                f"user folder flagged is_system: {body}"
            )
            user_folder_id = body["id"]

        with self.step("duplicate folder name at same parent → 409"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/folders",
                json={"name": user_folder_name},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 409, (
                f"expected 409 on duplicate name, got {resp.status_code}: {resp.text}"
            )

        docs_before = 0
        with self.step("count documents rows before"):
            docs_before = count_rows(
                cur,
                "documents",
                "tenant_id=%s AND user_id=%s AND deleted_at IS NULL",
                (self.cfg.tenant_id, self.user_id),
            )

        with self.step("POST /api/v1/documents/upload (multipart PDF into user folder)"):
            # Passing files= sets the multipart Content-Type. Don't
            # reuse self.session.headers['Content-Type'] if it's been set
            # elsewhere — httpx handles the boundary for multipart.
            assert user_folder_id is not None
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/documents/upload",
                files={
                    "file": (filename, pdf_bytes, "application/pdf"),
                },
                data={
                    "title": title,
                    "category": "livetest",
                    "folder_id": user_folder_id,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            assert "id" in body, f"POST response missing 'id': {body}"
            doc_id = body["id"]
            assert body.get("ocr_status") == "pending", (
                f"expected ocr_status=pending on upload, got "
                f"{body.get('ocr_status')!r}"
            )
            assert body.get("title") == title, (
                f"upload title mismatch: sent {title!r}, got {body.get('title')!r}"
            )
            assert body.get("folder_id") == user_folder_id, (
                f"folder_id mismatch: sent {user_folder_id!r}, got {body.get('folder_id')!r}"
            )
            assert body.get("sha256") == upload_sha256, (
                f"sha256 mismatch: expected {upload_sha256}, got {body.get('sha256')!r}"
            )

        with self.step("verify documents row delta (+1)"):
            after = count_rows(
                cur,
                "documents",
                "tenant_id=%s AND user_id=%s AND deleted_at IS NULL",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after == docs_before + 1, (
                f"delta {after - docs_before}, expected 1 "
                f"(before={docs_before}, after={after})"
            )

        with self.step("GET /api/v1/documents lists the new document"):
            assert doc_id is not None, "doc_id not set"
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/documents",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            entries = assert_pagination_envelope(resp.json(), "entries")
            assert any(d.get("id") == doc_id for d in entries), (
                f"document {doc_id} not in GET response "
                f"({len(entries)} entries returned)"
            )

        # OCR lifecycle: pending → processing → complete | failed.
        # Poll until the status is terminal (complete/failed); timeout
        # surfaces a wedged worker or a queue that isn't being consumed.
        final_status = "pending"
        page_count = 0
        terminal_states = {"complete", "failed", "not_needed"}
        with self.step(
            f"poll OCR completion (≤{OCR_POLL_TIMEOUT_SEC}s)"
        ):
            assert doc_id is not None
            deadline = time.monotonic() + OCR_POLL_TIMEOUT_SEC
            while time.monotonic() < deadline:
                resp = self.session.get(
                    f"{self.cfg.base_url}/api/v1/documents/{doc_id}",
                    timeout=self.cfg.timeout,
                )
                assert resp.status_code == 200, (
                    f"expected 200 on detail, got {resp.status_code}: {resp.text}"
                )
                detail = resp.json()
                status = detail.get("ocr_status")
                if status in terminal_states:
                    final_status = status
                    page_count = detail.get("page_count") or 0
                    break
                time.sleep(OCR_POLL_INTERVAL_SEC)
            else:
                raise AssertionError(
                    f"OCR did not reach a terminal state within "
                    f"{OCR_POLL_TIMEOUT_SEC}s for doc {doc_id} "
                    f"(last seen: {status!r})"
                )
            # 'complete' is the happy path. 'failed' proves pipeline
            # wiring but extraction failed; we only assert pages > 0
            # on the 'complete' branch. 'not_needed' shouldn't happen
            # for a PDF upload, but is accepted defensively.
            assert final_status in terminal_states, (
                f"unexpected ocr_status {final_status!r} for doc {doc_id}"
            )

        with self.step("verify document_pages rows exist (if OCR completed)"):
            assert doc_id is not None
            if final_status == "complete":
                pages_in_db = count_rows(
                    cur,
                    "document_pages",
                    "tenant_id=%s AND document_id=%s",
                    (self.cfg.tenant_id, doc_id),
                )
                assert pages_in_db >= 1, (
                    f"ocr_status=complete but document_pages has 0 rows "
                    f"for doc {doc_id} (detail said page_count={page_count})"
                )

        with self.step("GET /api/v1/documents/{id}/download returns original bytes"):
            assert doc_id is not None
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}/download",
                timeout=self.cfg.timeout,
                # Follow any redirect if the route serves from 'remote' tier;
                # for 'local'/'both' the response is a direct 200 and no
                # redirect happens anyway. (The client already follows
                # redirects by default; this is explicit for clarity.)
                follow_redirects=True,
            )
            assert resp.status_code == 200, (
                f"expected 200 on download, got {resp.status_code}: {resp.text[:200]}"
            )
            got_md5 = hashlib.md5(resp.content, usedforsecurity=False).hexdigest()
            assert got_md5 == upload_md5, (
                f"download MD5 {got_md5} != upload MD5 {upload_md5} "
                f"(got {len(resp.content)} bytes)"
            )

        # Annotations — delta-checked against document_annotations.
        ann_before = 0
        with self.step("count annotation rows before"):
            ann_before = count_rows(
                cur,
                "document_annotations",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )

        ann_body = f"{run_tag} annotation body"
        with self.step("POST /api/v1/documents/{id}/annotations"):
            assert doc_id is not None
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}/annotations",
                json={"body": ann_body},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, (
                f"expected 201, got {resp.status_code}: {resp.text}"
            )
            ann = resp.json()
            assert ann.get("body") == ann_body, (
                f"annotation body mismatch: sent {ann_body!r}, got {ann.get('body')!r}"
            )
            ann_id = ann.get("id")
            assert ann_id, f"annotation response missing id: {ann}"

        with self.step("verify annotation row delta (+1)"):
            after_ann = count_rows(
                cur,
                "document_annotations",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert after_ann == ann_before + 1, (
                f"annotation delta {after_ann - ann_before}, expected 1 "
                f"(before={ann_before}, after={after_ann})"
            )

        with self.step("GET /api/v1/documents/{id}/annotations lists the annotation"):
            assert doc_id is not None and ann_id is not None
            resp = self.session.get(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}/annotations",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            body = resp.json()
            anns = body.get("annotations", [])
            assert any(a.get("id") == ann_id for a in anns), (
                f"annotation {ann_id} not in GET response "
                f"({len(anns)} annotations returned)"
            )

        with self.step("PATCH annotation body"):
            assert doc_id is not None and ann_id is not None
            resp = self.session.patch(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}"
                f"/annotations/{ann_id}",
                json={"body": f"{ann_body} (updated)"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("DELETE annotation"):
            assert doc_id is not None and ann_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}"
                f"/annotations/{ann_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            final_ann = count_rows(
                cur,
                "document_annotations",
                "tenant_id=%s AND user_id=%s",
                (self.cfg.tenant_id, self.user_id),
            )
            assert final_ann == ann_before, (
                f"post-delete annotation count {final_ann}, expected {ann_before}"
            )

        # Phase 2: rename folder, move document, rename document.
        renamed_folder = f"{user_folder_name}-renamed"
        with self.step("PATCH /api/v1/folders/{id} renames the user folder"):
            assert user_folder_id is not None
            resp = self.session.patch(
                f"{self.cfg.base_url}/api/v1/folders/{user_folder_id}",
                json={"name": renamed_folder},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            assert resp.json().get("name") == renamed_folder

        with self.step("system folder rename is rejected (403)"):
            assert docs_folder_id is not None
            resp = self.session.patch(
                f"{self.cfg.base_url}/api/v1/folders/{docs_folder_id}",
                json={"name": "NotDocuments"},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 403, (
                f"expected 403, got {resp.status_code}: {resp.text}"
            )

        with self.step("PATCH /api/v1/documents/{id} moves doc to Documents folder"):
            assert doc_id is not None and docs_folder_id is not None
            resp = self.session.patch(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}",
                json={"folder_id": docs_folder_id},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            assert resp.json().get("folder_id") == docs_folder_id, (
                f"move failed: response folder_id={resp.json().get('folder_id')!r}"
            )

        with self.step("PATCH /api/v1/documents/{id} renames filename"):
            assert doc_id is not None
            new_name = f"{run_tag}-renamed.pdf"
            resp = self.session.patch(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}",
                json={"filename": new_name},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            assert resp.json().get("filename") == new_name, (
                f"rename failed: got {resp.json().get('filename')!r}"
            )

        with self.step("DELETE /api/v1/documents/{id} (soft)"):
            assert doc_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )

        with self.step("verify documents non-deleted count returns to baseline"):
            final_docs = count_rows(
                cur,
                "documents",
                "tenant_id=%s AND user_id=%s AND deleted_at IS NULL",
                (self.cfg.tenant_id, self.user_id),
            )
            assert final_docs == docs_before, (
                f"post-delete count {final_docs}, expected {docs_before}"
            )

        with self.step("verify deleted_at set in DB"):
            assert doc_id is not None
            cur.execute(
                "SELECT deleted_at FROM documents "
                "WHERE tenant_id=%s AND id=%s",
                (self.cfg.tenant_id, doc_id),
            )
            row = cur.fetchone()
            assert row is not None, f"document {doc_id} vanished from DB"
            assert row["deleted_at"] is not None, (
                f"soft-delete left deleted_at NULL for doc {doc_id}"
            )

        with self.step("POST /api/v1/documents/{id}/restore brings it back"):
            assert doc_id is not None
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/documents/{doc_id}/restore",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, (
                f"expected 200, got {resp.status_code}: {resp.text}"
            )
            assert resp.json().get("restored") is True

        # Phase 2: folder trash cascades to documents; restore reverses both.
        cascade_folder_id: str | None = None
        cascade_doc_id: str | None = None
        cascade_folder_name = f"cascade-{run_tag}"
        with self.step("create cascade-test folder + doc"):
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/folders",
                json={"name": cascade_folder_name},
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, resp.text
            cascade_folder_id = resp.json()["id"]

            cascade_pdf = _build_sample_pdf(f"CASC-{run_tag[-6:].upper()}")
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/documents/upload",
                files={"file": ("cascade.pdf", cascade_pdf, "application/pdf")},
                data={
                    "title": f"{run_tag}-cascade",
                    "folder_id": cascade_folder_id,
                },
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 201, resp.text
            cascade_doc_id = resp.json()["id"]

        with self.step("DELETE /api/v1/folders/{id} cascades to documents"):
            assert cascade_folder_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/folders/{cascade_folder_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body.get("folders_trashed") == 1, (
                f"expected 1 folder trashed, got {body.get('folders_trashed')}"
            )
            assert body.get("documents_trashed") == 1, (
                f"expected 1 document trashed, got {body.get('documents_trashed')}"
            )

        with self.step("cascaded document is soft-deleted in DB"):
            assert cascade_doc_id is not None
            cur.execute(
                "SELECT deleted_at FROM documents WHERE tenant_id=%s AND id=%s",
                (self.cfg.tenant_id, cascade_doc_id),
            )
            row = cur.fetchone()
            assert row is not None and row["deleted_at"] is not None, (
                f"cascade failed to trash doc {cascade_doc_id}"
            )

        with self.step("system folder trash is rejected (403)"):
            assert fax_folder_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/folders/{fax_folder_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 403, (
                f"expected 403, got {resp.status_code}: {resp.text}"
            )

        with self.step("POST /api/v1/folders/{id}/restore cascades to documents"):
            assert cascade_folder_id is not None
            resp = self.session.post(
                f"{self.cfg.base_url}/api/v1/folders/{cascade_folder_id}/restore",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body.get("folders_restored") == 1, (
                f"expected 1 folder restored, got {body.get('folders_restored')}"
            )
            assert body.get("documents_restored") == 1, (
                f"expected 1 doc restored, got {body.get('documents_restored')}"
            )

        with self.step("restored document has deleted_at=NULL in DB"):
            assert cascade_doc_id is not None
            cur.execute(
                "SELECT deleted_at FROM documents WHERE tenant_id=%s AND id=%s",
                (self.cfg.tenant_id, cascade_doc_id),
            )
            row = cur.fetchone()
            assert row is not None and row["deleted_at"] is None, (
                f"restore failed to clear deleted_at for doc {cascade_doc_id}"
            )

        # Soft-delete the cascade doc so it doesn't linger in the user's folders.
        with self.step("cleanup: soft-delete cascade test document"):
            assert cascade_doc_id is not None
            resp = self.session.delete(
                f"{self.cfg.base_url}/api/v1/documents/{cascade_doc_id}",
                timeout=self.cfg.timeout,
            )
            assert resp.status_code == 200, resp.text

        return self.result()


def main() -> None:
    cfg = load_config(sys.argv[1:])
    session = login(cfg)
    resp = session.get(
        f"{cfg.base_url}/api/v1/session", timeout=cfg.timeout
    )
    resp.raise_for_status()
    user_id = resp.json()["user_id"]

    conn = open_rls_connection(cfg, user_id)
    try:
        flow = DocumentsFlow(cfg, session, conn, user_id)
        result = flow.run()
    finally:
        conn.close()

    print_live(result)
    report_path = write_markdown([result], cfg)
    print(f"\nReport: {report_path}")
    sys.exit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()

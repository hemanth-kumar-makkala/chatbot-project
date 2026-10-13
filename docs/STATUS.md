# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile — **COMPLETED** (Exit gate verified in `docs/phases/PHASE-1-GATE.md`)
- **Phase 2**: Ingestion Pipeline (Runs on office PC then desktop-gpu; exit gate: Golden docs ingested twice with zero duplicates)

## Current Task
- **Task 2.8a Step 3 (a & b)**: Knowledge Review Actions, Promote & Retry — **COMPLETED**
  - Completed: Promote preconditions: promote is allowed strictly when version status is `needs_review`; `processing`, `failed`, `retired`, `received` return 409 `INVALID_VERSION_STATE`; promoting an already-active ready version is an idempotent no-op 200.
  - Completed: Retry route `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/retry` with client_admin RBAC, CSRF token validation, and assistant tenant anti-probing 404s.
  - Completed: Retry eligibility checks under row locks (`Document` -> `DocumentVersion` -> `Page`):
    - Refuses with 409 `DocumentRetiredError` if document is retired.
    - Refuses with 409 `INVALID_PAGE_STATE` if page is clean (`needs_review=False`).
    - Refuses with 409 `PAGE_JOB_ALREADY_ACTIVE` if a background job for that page is already queued or running (`JobStep.status in ('processing', 'todo', 'doing')`).
    - Refuses with 409 `RETRY_NOT_USEFUL` if a `page_readings` row exists for the failed step with current model/version and processing version unchanged.
    - Allows retry when no reading exists for the failed step (e.g. `vision_failed`), or when processing version has changed (e.g. from `v0` to `v1`).
  - Completed: Retry enqueuing keeps `page.needs_review = True` (stays flagged until retried run finishes) and records audit event `page_retry_enqueued`.
  - Completed: Post-retry version re-evaluation under row locks (`Document` -> `DocumentVersion` -> `Page`):
    - Live version: activates page chunks, and if 0 flagged pages remain, transitions version status from `needs_review` to `ready` (Branch B).
    - Branch A version: if 0 flagged pages remain, auto-promotes version (`doc.active_version_id = version_id`, `ver.status = 'ready'`, retires prior version & deactivates prior chunks, activates clean chunks), and writes separate compliance audit event `version_auto_promoted` with actor recorded as system (`user_id=None`).
  - Completed: Procrastinate deferral order: `retry_page` route dispatches `process_page_job.defer_async` inside the `tenant_session` transaction before commit, guaranteeing that a failed defer rolls back `JobStep.status = 'processing'` and leaves the page retryable without sticking in `PAGE_JOB_ALREADY_ACTIVE`.
  - Completed: Worker pipeline integration: `VersionActivationService.check_and_activate_if_complete` invokes `re_evaluate_version_after_retry` upon retried page completion for `needs_review` and `ready` versions; verified via end-to-end integration test through `run_page_job`.
  - Completed: Retry flag clearing constraints: passing retried page clears only reasons the re-run is responsible for (`processing_failed`, `vision_failed`, `image_too_large`, `low_ocr_confidence`), never clears `legacy_unspecified`, never replaces `legacy_unspecified` with another reason on re-runs (SQL `case` preservation), and never alters excluded pages.
  - Completed: 26 integration tests in `test_api_knowledge_step3.py` and 24 invariant tests passing cleanly.
  - Completed: Pre-commit checks pass on all modified files.
- **Task 2.8a Step 2**: Knowledge Page Review List, Image Proxy & Retire — **COMPLETED**
  - Completed: Migration 0008 (`0008_excluded_and_retired.py`) with `pages.is_excluded`, `documents.is_retired`, and check constraint `ck_pages_excluded_needs_review` (`NOT (is_excluded = true AND needs_review = true)`). Tested on throwaway database only; unapplied to shared DB.
  - Completed: Review list endpoint (`GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/review`) returning flagged pages, review reasons, ocr confidence, readings, and image flag; compliance audit event (`review_inspected`).
  - Completed: Page image proxy (`GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/image`) returning image bytes via storage port with `Content-Type: image/png` and `Cache-Control: private, no-transform, max-age=3600`; compliance audit event (`page_image_viewed`).
  - Completed: Retire document endpoint (`DELETE /v1/assistants/{assistant_id}/knowledge/documents/{document_id}`) with double-submit CSRF enforcement, setting `is_retired = True`, clearing `active_version_id = None`, deactivating all chunks, marking versions retired; compliance audit event (`document_retired`).
  - Completed: Concurrency serialization: Version activation gated under document row lock (`doc.is_retired`), refusing activation if retired (`check_and_activate_if_complete` returns False, `activate_version` raises `DocumentRetiredError`). Tested concurrency race: document retired during processing -> version never goes live.
  - Completed: Anti-probing 404s for cross-tenant version/doc/page/assistant and RBAC 403s for staff users across all routes.
  - Completed: 2 migration tests (`test_migration_0008.py`) and 5 API/concurrency integration tests (`test_api_knowledge_step2.py`) passing.
- **Task 2.8a Step 1**: Knowledge Page Backend API (Upload Endpoint & Status List) — **COMPLETED**
  - Completed: Assistant-scoped routes `/v1/assistants/{assistant_id}/knowledge/upload` and `/v1/assistants/{assistant_id}/knowledge/documents`.
  - Completed: Tenant derived via `lookup_assistant_tenant` and client_admin role checked via `auth_provider.roles` strictly as `app_user` (zero owner role `cka_admin` connections in request paths).
  - Completed: Invariant test `test_invariant_api_never_uses_owner_role_session` verifying AST ban of `cka_admin`, `admin_session`, `get_admin_engine`, and `get_admin_session_factory` in `app/api/`.
  - Completed: `POST .../upload` with memory-only streaming up to `max_upload_bytes` (never invoking Starlette's `SpooledTemporaryFile`), CSRF double-submit verification, anti-probing 404 for cross-tenant replace and unknown assistant, compliance audit logging (`document_uploaded`), and Procrastinate split job dispatch with IDs only.
  - Completed: `GET .../documents` status list deriving UI states from allowlist (`Queued`, `Reading`, `Scanning images`, `Ready`, `Ready (N pages need review)`, `Failed`, `Retired`), raising error on unknown status (never falling back to `Queued`), with zero audit events logged on status list polling.
  - Completed: 10 integration tests in `tests/integration/test_api_knowledge_endpoints.py` and 24 boundary invariant tests passing with 0 warnings.
- **Task 2.5**: Upload-Time Vision Integration (Mock Profile & Pipeline Wiring) — **COMPLETED**
  - Completed: `ADR-0011` (upload-time vision reading port method and optional confidence), `0007_chunks_reading_evidence.py` applied to shared database, `PillowImageProcessor` adapter and `ImageProcessor` port, `ReviewReason` required literal type, `PagePipelineService` per-page upload-time vision transcription, review flag recovery (Option A), dual reading selection rule (reliable text as text, vision as visual, low-OCR text never chunked), re-run idempotency, fail-closed non-mock wiring guards in `create_page_pipeline_service`, zero text leakage invariant (`test_upload_vision_zero_text_leakage_in_job_steps_and_audit`), Branch B activation on permanent vision error (`test_upload_vision_permanent_error_flags_review_and_activates_branch_b`), and 12 integration tests in `test_upload_vision.py`.
- **Task 2.7**: Ingestion Pipeline Worker & Version Activation — **implementation done, awaiting verification**
  - Completed: Alembic migration `0006_procrastinate_schema.py` (procrastinate schema, table grants for `app_user`, revoked for `retriever_user`, unapplied to shared DB), database tests in `test_migration_0006.py`, worker/tenant session separation (`worker_tenant_session(WorkerContext)` vs `tenant_session(TenantContext)`), system-worker audit actor (`user_id=None`), split job crash re-run recovery (`enqueue_uncompleted_page_jobs`), deterministic scoped chunk UUIDs, `PostgresDocumentRepository` version row lock (`SELECT ... FOR UPDATE`) and uncompleted page calculation, `PagePipelineService` with transient retry vs permanent review flagging, and atomic `VersionActivationService` (Branches A, B, C with atomic version flip and previous version retirement).
- **Task 2.6**: Tabular Data Ingestion (CSV / Excel Worksheets into `tables_data`) — **COMPLETED**
  - Completed: `BuiltinTableExtractor` with dual-read openpyxl distinguishing formulas lacking cached values (`unreadable` list in row JSON, zero sentinel strings in cell values), typed stubs (`types-openpyxl==3.1.5.20260827`, `types-defusedxml==0.7.0.20260504`), `defusedxml` XML bomb guard verified, CSV delimiters (`,`, `;`, `\t`), encodings (UTF-8, UTF-8 BOM, CP1252), strict limits (200 cols, 50,000 rows, 1,000,000 cells), date formatting by cell number format, merged cells verbatim retention, hidden sheet skip reporting, `TableIngestionService` with 1,000-row batching and `job_steps` recording (step recorded with status, zero sheet names), and `uq_tables_data_idempotency` re-run idempotency.
- **Task 2.4**: Docling Parser Adapter & Tri-Path Extraction — **code complete, awaiting desktop verification**
  - Completed: Review policy with MECE coverage for all 4 page types, Migration 0005 applied to shared database (`0005_readings_table_cites`), database seeding test fixtures decoupled to `tests/fixtures/database_seeding.py`, parser profile matrix routing (`mock` -> `MockDocumentParser`, `office-cpu`/`desktop-gpu`/`home-gpu`/`dev-free-api` -> `DoclingDocumentParser`, `cloud` -> `DoclingDocumentParser` with offline/pinned models enforced), and adapter offline configuration (`EasyOCR` download disabled, model directory set, `HF_HUB_OFFLINE=1`).

## Desktop Verification Pending
Phase 2 cannot pass its exit gate until this list is empty:
1. `tests/integration/test_docling_parser.py::TestRealDoclingModelsDesktop::test_real_docling_ocr_spanish_scanned_page` (Spanish scanned page with EasyOCR OCR engine)
2. `tests/integration/test_docling_parser.py::TestRealDoclingModelsDesktop::test_real_docling_digital_text_and_table_extraction` (Digital PDF text and structured table data extraction)
3. `tests/integration/test_docling_parser.py::TestRealDoclingModelsDesktop::test_real_docling_multipage_confidence_and_review_detection` (Multi-page document per-page confidence scores and review gating)
4. Real BGE-M3 embedding verification (Phase 2 Task 2.7 chunking and embedding real-model test)

## Next Task
- **Task 2.5**: Vision Model Integration on Mock Provider (or Task 2.8: Review Flagging & Knowledge Admin UI)

## Pending Implementation Notes
- **Upload API (Phase 2 upload API task)**: The upload endpoint must open `tenant_session(ctx)` itself and pass that session to `create_upload_service`. Add a test in that task proving uploads run with `app.current_tenant_id` set.
- **Version Activation (Phase 2 activation task)**: Version activation must flip `documents.active_version_id` and `chunks.is_active` in one transaction, with a test, in the task that builds activation.
- **Storage Adapter (Phase 2 storage task)**: `StorageKey` must take `tenant_id` from `TenantContext` as a UUID and reject `..`, absolute paths, and backslashes in Python as well.
- **Parser Wiring Profile Guard**: `MockDocumentParser` is strictly wired only in profile `mock`. `office-cpu`, `desktop-gpu`, `home-gpu`, and `dev-free-api` wire `DoclingDocumentParser`. `cloud` profile strictly enforces `disable_model_download=True` and `pinned_model_paths` and forwards them to `DoclingDocumentParser`. All unknown profiles raise `ConfigurationError`.
- **Table Export Formula Escaping**: In `tables_data`, cell values must be stored exactly as written (unmodified raw values, e.g. `-150`). Formula injection sanitization (prefixing risky leading characters `=`, `+`, `-`, `@`, `\t`, `\r` with `'`) must be applied strictly during client export to CSV/Excel in the export endpoint/UI, never in database storage.
- **Existing Flagged Pages & Migration 0007**: Pages flagged before migration 0007 with `needs_review = true` and `review_reason IS NULL` are migrated to `review_reason = 'legacy_unspecified'` in migration 0007. The constraint `ck_pages_needs_review_reason` is enforced as a fully valid check constraint. Upload-time vision does not auto-clear pages flagged with `legacy_unspecified`.
- **Knowledge Page Review Activation (Phase 2 Task 2.8)**: After an admin resolves flagged pages in the Knowledge page UI, version activation re-runs for that document version (allowing Branch A versions previously held in `needs_review` to become `ready` and retire the previous active version).
- **Review Page Re-running (Phase 2 Task 2.8)**: Re-running failed pages after client admin review belongs to Task 2.8.
## Blockers
- **None for Phase 1**: Phase 1 operates entirely on the `mock` profile with synthetic data on the 8 GB office PC (Docker memory capped at 3 GB).
- **External Governance Gates (Phase 6/7 Prerequisites)**:
  - Pharmacy system Gemini key migration to Vertex AI under GCP BAA.
  - Formal retention periods agreement with Dan and compliance.
  - Pilot client practice BAA execution before live data ingestion.

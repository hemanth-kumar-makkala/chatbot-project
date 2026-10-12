# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile — **COMPLETED** (Exit gate verified in `docs/phases/PHASE-1-GATE.md`)
- **Phase 2**: Ingestion Pipeline (Runs on office PC then desktop-gpu; exit gate: Golden docs ingested twice with zero duplicates)

## Current Task
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

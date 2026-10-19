# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile — **COMPLETED** (Exit gate verified in `docs/phases/PHASE-1-GATE.md`)
- **Phase 2**: Ingestion Pipeline (Runs on office PC then desktop-gpu; exit gate: Golden docs ingested twice with zero duplicates)

- **Task UI-3: Finish all Phase 3 UI (Chat fixes, Real Thumbnails, Status Lines, Client Switcher, Source Panel)** — **COMPLETED (Parts 0–D & Part E Component; STOP at pdfjs-dist install)**
  - **Phase 2 Gate Status**: Phase 2 gate not yet passed; root cause is the gate test polling `DocumentVersion` through `unscoped_session` under PostgreSQL RLS (test-only bug where `app_user` returns 0 rows without tenant context setting). Next backend task is fixing that and rerunning the gate.
  - **Vision Calibration Status**: Calibrated on golden set (`evals/reports/vision_calibration.json`). Misread scored 0.934 vs correct 0.917; confidence alone cannot separate them; threshold decision open; 0.70 threshold unchanged.
  - **Part A (UI-2 Fixes)**: INJ-001 renders as normal grounded answer citing `compliance_notice.pdf`; status lines display dynamic cited page `N` (never hardcoded 31); VR-007 cites `skewed_fax.pdf` with golden expected values; clarify and general draft scenarios labeled demo-only with no golden IDs.
  - **Part B (Page Thumbnails)**: `backend/scripts/render_demo_pages.py` rendered all 30 golden PDF pages to PNG thumbnails and view images in `web/public/demo/pages/` and copied golden PDFs to `web/public/demo/docs/`. Real thumbnail renders in chat and opens source panel on click.
  - **Part C (Status Lines Delay)**: Sequential status lines appear with short delay on demo assistants; live assistant keeps loading indicator only until Task 3.6 SSE.
  - **Part D (Client Switcher)**: Switcher dropdown mounted in `TopBar.tsx` using `DEMO_ASSISTANTS` fixtures, navigating to selected assistant's chat.
  - **Part E (Source Panel Component & STOP)**: `web/components/SourcePanel.tsx` built with page navigation `#page=N`, preview controls, and live assistant disclaimer (`"Source preview needs the document file route (not built yet)"` with `Demo` chip). STOP reached for exact pinned `pdfjs-dist` npm install command.
  - **Verification**: `web/__tests__/ui3.test.tsx` (9 tests), all 8 web test suites (78 tests), and `npm run typecheck` pass cleanly.
  - **Next Backend Task**: Fix test-only `DocumentVersion` polling bug under RLS and rerun Phase 2 gate.

- **Adapter 3: BGE-M3 Embeddings Adapter — COMPLETED & VERIFIED** (Preceding task)
  - **Part A (Chunker Guards)**:
    - Chunk numbering continues contiguously across all readings in one `chunk_page` call (`chunk_offset` passed to `_chunk_single_reading`, zero chunk index collisions).
    - Pipeline duplicate evidence type guard enforces that two selections of the same evidence type on one page raise `DuplicateEvidenceTypeError`. Nothing stored.
  - **Part B (Adapter Implementation & Profile Config)**:
    - `backend/app/adapters/embedding/bge_m3.py` created with `BgeM3EmbeddingProvider` implementing `EmbeddingProvider`.
    - Profile validation enforces all required keys (`model`, `revision`, `device`, `max_seq_length`). Device allowlist restricted to `{"cpu", "cuda"}`; `cuda` configured when `torch.cuda.is_available()` is False raises `ConfigurationError`. Never falls back.
    - Offline loading: `SentenceTransformer` loaded with `local_files_only=True` and pinned revision `5617a9f61b028005a4858fdac845db406aefb181`. Missing model raises `ConfigurationError` with fixed message. Snapshot hash mismatch raises typed `ConfigurationError`.
    - Imports `sentence_transformers` only inside the loader; `mock` profile never imports it. Adapter accepts model via `SentenceTransformerModelProtocol`.
    - `BATCH_SIZE = 32` named constant; `asyncio.to_thread` with `normalize_embeddings=True`.
    - Tokenizer token length guard raises `EmbeddingInputTooLongError` for inputs over `max_seq_length` (8192); zero silent truncation.
    - Output validation verifies vector count equals input count, dimension is 1024, and all values are finite; otherwise raises `EmbeddingOutputInvalidError`.
    - Wired in `create_embedding_provider` for `desktop-gpu` and `home-gpu` (`device: cpu`, `max_seq_length: 8192`, pinned revision).
  - **Part C (Pipeline Context Prefix)**:
    - Embed input formatted as `context_prefix + "\n\n" + chunk.text`; stored chunk text remains the exact verbatim substring. Embedding count mismatch raises `EmbeddingCountMismatchError`.
  - **Part D (Real-Model Test)**:
    - Test `test_real_bge_m3_model` marked `models` in `backend/tests/unit/test_bge_m3_adapter.py`: verifies 1024 dims, L2 norm within 1e-3 of 1.0, similar pair cosine > unrelated pair cosine, and determinism. Prints execution time.
  - **Next Task**: Real Docling desktop tests, then real fax upload on desktop-gpu.

- **Laptop Setup Verification & Hook Portability** — **COMPLETED & VERIFIED** (Preceding task)
  - **Environment Verification**: Full backend test suite on mock profile verified (576 passed, 1 skipped, 0 failed). Web unit tests verified (69 passed across 7 test files). PyTorch CUDA acceleration verified (`torch==2.14.1+cu126`, CUDA enabled on RTX 3060 Laptop GPU).
  - **Hook Portability**: Local pre-commit hooks `mypy` and `boundary-invariants` in `.pre-commit-config.yaml` updated to invoke via `python -m`, resolving Windows Application Control blocking `.venv\Scripts\*.exe` (`os error 4551`). All 10 pre-commit hooks passing cleanly.
  - **Path Normalization**: `docs/ui/BUILD_NOTES.md` local run commands updated to repo-root relative paths (`cd backend`, `cd web`).

- **Task UI-2: Every Answer Type in Chat, Status Lines & Demo Scenarios (UI-First)** — **COMPLETED & VERIFIED** (Preceding task)
  - **Part A (Authoritative Spec Quote & Section Alignment)**: Fixed all non-spec section names across `UI_INVENTORY.md` and `CONNECTION_MAP.md` strictly to the 21 authoritative spec sections. Corrected verbatim quotes for Client Admin flow step 4, Query flow Node 2, Invariant 5, and Staff day step 4. Audited every remaining double-quoted string: 32/32 quotes verified verbatim in `CONNECTION_MAP.md`; 110/110 quotes verified verbatim in `UI_INVENTORY.md`.
  - **Answer Presentation Types**:
    - Standard text answer with interactive source chips (`document_title`, `version`, `page_number`) and source language label (`Source (Spanish)` / `[Spanish]`, spec section "Query flow" ("Language")).
    - Table answer citing sheet name and row number (`accepted_plans.csv, Sheet: Plans, Row 4`, spec section "Query flow" Answer modes "table").
    - Visual answer marked as read from an image (`Visual Reading` lavender badge) with clearly labelled page thumbnail placeholder frame (`[Page Thumbnail Placeholder]`, Invariant 5).
    - Refusal card rendering exact spec text: `"I don't have that in Client A's documents."`
    - Conflict presentation rendering `"Two documents disagree: (Conflicting Sources)"` with side-by-side citations for both sources.
    - Clarification Needed presentation displaying top 3 topics as interactive buttons; clicking one submits that question immediately.
    - General task draft labelled with ungrounded warning badge (`General Task Draft — Not grounded in client documents`, spec section "Goals, non-goals and success criteria" Goal 6, "Query flow" Node 2).
    - Safe failure card masking system errors with public reference (`ERR-xxxx` / `Reference: ERR-7K42`, spec section "Failure handling and fallbacks").
    - Safe friendly fallback message when receiving unexpected response types.
  - **Status Lines**: Demo assistants demonstrate spec status lines (`"Searching Client A documents..."`, `"Page 31 is a scanned image, reading it now..."`, spec section "End result: how people will use it" step 4) loaded from fixtures; live assistant displays only standard loading indicator until SSE endpoint exists (Task 3.6), never faking status lines on live.
  - **Demo Scenarios (Empty Chat State)**: In empty chat for demo assistants, renders suggested questions for every answer type, strictly cited from `evals/golden/questions.json` (`TF-003`, `TL-011`, `VR-007`, `NOA-002`, `CONF-001`, `TF-001`, `TF-010`, `INJ-001`).
  - **Tests**: `web/__tests__/ui2.test.tsx` (12 tests) passing; full web test suite (69 tests across 7 suites) passing cleanly with zero regressions. TypeScript typecheck passing.
  - **Next Task**: `UI-3` (Embedded PDF.js viewer side panel and staff document byte streaming route per UI inventory)

- **Task UI-1: Assistant Picker & Chat Layout with Conversation History (UI-First)** — **COMPLETED & REVIEWED** (Preceding task)

- **Task UI-0: UI Inventory & Connection Map Across All Phases** — **COMPLETED** (Preceding task)

- **Phase 3 Chat Screen on Mock Profile** — **COMPLETED** (Preceding task)
- **Adapter 2**: Structure-Aware Real Chunker (all non-mock profiles) — **COMPLETED**
  - Completed: `StructureAwareChunker` in `backend/app/adapters/chunking/structure_aware.py` implementing `Chunker`.
  - Completed: Page-first design: chunks never cross page boundaries. Empty or whitespace pages return zero chunks.
  - Completed: Structure-aware: splits on headings and paragraphs first, long paragraphs on sentences with sliding window overlap (`TARGET_CHUNK_OVERLAP_WORDS = 50`) and minimum chunk size (`MIN_CHUNK_SIZE_WORDS = 20`) merging trailing sentences.
  - Completed: Large tables split into whole rows without splitting table rows; header row preserved in `context_prefix` and not repeated in chunk text.
  - Completed: Exact substring guarantee: stored chunk text is an exact substring of the original page text without alteration.
  - Completed: Context prefix (`{document_title} - Page {page_number} - {heading}`) stored separately from chunk text.
  - Completed: `ChunkingContext` typed dataclass carrying `tenant_id`, `document_version_id`, `processing_version`, `page_number`, `evidence_type`, and `document_title`.
  - Completed: Chunk UUIDs derived deterministically (`uuid5`) strictly from `(tenant_id, document_version_id, processing_version, page_number, evidence_type, chunk_index)` only (not from title or text).
  - Completed: Direct repository title lookup: `get_document_title_for_version` added to port and `PostgresDocumentRepository`, executed once per page job in pipeline (no `list_documents_with_status` loop, no f-string error).
  - Completed: Explicit evidence type map `_EVIDENCE_TYPE_MAP` in pipeline; unknown value raises typed `InvalidEvidenceTypeError` with zero silent fallback.
  - Completed: Embedding count parity enforced: `len(embeddings) != len(chunks)` raises typed `EmbeddingCountMismatchError`, guaranteeing zero chunks stored without embeddings.
  - Completed: `TextChunk.chunk_id` mapped directly to `chunks.id` on DB batch insert.
  - Completed: Wired in `create_chunker` in `app/wiring.py` with fixed error message (no f-string) for unknown profiles.
  - Completed: Unit tests in `test_structure_aware_chunker.py` (11/11 passed), wiring tests in `test_worker_and_activation.py` and `test_wiring_profile_guards.py` (44/44 passed), pipeline integration tests (23/23 passed), and architecture invariants (25/25 passed) passing cleanly. Pre-commit 10/10 hooks passing.
- **Adapter 1**: Real Ollama Model Provider (`desktop-gpu` and `home-gpu` profiles) — **COMPLETED**
  - Completed: `OllamaModelProvider` in `backend/app/adapters/models/ollama.py` implementing `ModelProvider`.
  - Completed: `OLLAMA_BASE_URL` required env var with no default; missing raises `ConfigurationError`.
  - Completed: Dynamic role-to-model configuration loaded from `config/profiles/{profile}.yaml` (`qwen3-vl:4b` for vision on desktop-gpu).
  - Completed: `transcribe_page` scales page image with `ImageProcessor` to fit within 6 GB VRAM, invokes Ollama `/api/generate` with fixed verbatim prompt and deterministic generation options (`temperature: 0.0`, `seed: 42`, `top_k: 1`, `top_p: 1.0`).
  - Completed: Confidence calculated as geometric mean `exp(mean(logprob))` over text tokens, excluding special/structural tokens (`<|...|>`, `<s>`, `</s>`, `<think>`, etc.). Missing or empty logprobs returns `confidence=None` flagging page as `vision_confidence_missing`.
  - Completed: Timeout and connection errors mapped to `TransientIngestionError` for worker retry; malformed responses mapped to typed permanent errors (`MalformedOutput`, `OllamaPermanentError`).
  - Completed: Unused methods (`generate`, `read_page`, `extract_fields`) raise typed `OllamaMethodNotImplementedError` with fixed message.
  - Completed: Wired in `create_model_provider` in `app/wiring.py` for `desktop-gpu` and `home-gpu` profiles only.
  - Completed: 17 unit tests in `test_ollama_provider.py` (including deterministic payload options and fuzzy phrase matcher) and 33 wiring profile tests passing cleanly; 1 integration test marked `models` with legible synthetic fax (`ImageFont.load_default(size=44)`), robust fuzzy phrase matching, and stdout print for `-s`.
  - Completed: `types-pyyaml==6.0.12.20260906` installed in dev group; removed `yaml` mypy override while retaining targeted docling/easyocr overrides.
- **Task 2.8c**: Wire Real Ingestion Path & Migration 0009 — **COMPLETED**
  - Completed: Migration 0009 (`0009_version_file_extension`) adding `document_versions.file_extension` (String 20) with check constraint `ck_document_versions_file_extension` enforcing NULL or exact accepted upload extensions ('pdf', 'png', 'jpeg', 'tiff', 'xlsx', 'csv', 'txt'). Tested in throwaway DB.
  - Completed: Storage backend wired to `LOCAL_STORAGE_PATH` env var with no default; missing or non-existent folder raises `ConfigurationError`.
  - Completed: Worker `run_split_document_job` loads original file from storage using `ver.file_extension` with zero guessing.
  - Completed: Path A (PDF): calls `DocumentSplitterService.split_document_version` creating `Page` rows and rendered page images, updates status to `processing`, and enqueues `process_page_job`.
  - Completed: Path B (CSV / XLSX): calls `TableIngestionService.ingest_table_file`, populates `tables_data` rows, and transitions version to `ready` directly with zero page jobs.
  - Completed: All 4 E2E tests in `test_procrastinate_worker_e2e.py` (with zero hand-seeded pages), 30 upload validation tests, 2 migration tests, and entire backend pytest suite (231 passed, 1 skipped) passing cleanly.
- **Task 2.8b**: Knowledge Page Web App — **COMPLETED**
  - Completed: Saved `docs/ui/KNOWLEDGE_PAGE_SPEC.md` with Staffingly brand tokens, screens, review reasons, and accessibility rules.
  - Completed: Audited backend route implementations in `docs/ui/API_CONTRACT_NOTES.md` with file/line citations covering auth, cookies (`session_token`, `csrf_token`), `X-CSRF-Token`, knowledge routes, error codes, port 8000, and CORS origin settings.
  - Completed: Scaffolded `web/` with Next.js App Router, React 19, TypeScript strict, and plain CSS variables. Pinned all 14 packages to exact stable versions published on or before 2026-09-30 (>= 14 days ago).
  - Completed: Built `styles/tokens.css`, `styles/globals.css`, `lib/api.ts` (fetch wrapper with credentials include, CSRF token injection on POST/DELETE, typed error masking hiding raw server text), `lib/reviewReasons.ts`, and `lib/statusChips.ts`.
  - Completed: Built `TopBar.tsx` with Staffingly wordmark, gradient, and logout menu, and `/login` page with cookie-based session and accessible error states.
  - Completed: Built `/assistants/[assistantId]/knowledge` page with `UploadArea` (drag/drop, 50MB and PDF/XLSX/CSV pre-validation), `DocumentsTable` with status chips, `ConfirmDialog` for document retirement, and 5-second polling that stops when all documents are in final states (`ready`, `failed`, `retired`) or when the tab is hidden.
  - Completed: Built `ReviewDrawer` displaying flagged pages, page images, extracted text, per-page Accept/Exclude/Retry actions, Promote Version action, 409 job-active message, and error sanitization hiding raw server text.
  - Completed: 32 unit tests across `smoke.test.ts`, `foundations.test.ts`, `knowledge_page.test.tsx`, and `review_drawer.test.tsx`.
  - Completed: Full documentation in `docs/ui/BUILD_NOTES.md`.
- **Task A2**: Per-Page Job Active Marker (`page_processing`) — **COMPLETED**
  - Completed: `JobStepStatus` typed as `Literal["queued", "processing", "running", "completed", "failed"]` in `app/ports/types.py`. Kept `"processing"` because `pipeline.py:156` (`vision`) and `splitter_service.py:72` (`split`) still write it.
  - Completed: `ACTIVE_PAGE_MARKER_STATUSES: frozenset[JobStepStatus] = frozenset({"queued", "running"})` and `CURRENT_PROCESSING_VERSION: str = "v1"` in `app/ports/types.py`.
  - Completed: `_assert_no_active_page_job` checks ONLY `step="page_processing"` with `status in ACTIVE_PAGE_MARKER_STATUSES`. Other steps can never block accept/exclude/retry again.
  - Completed: `record_page_processing_marker` added to port and `PostgresDocumentRepository` locking Page row FOR UPDATE and upserting via `on_conflict_do_update` (`status`, `job_id`, `updated_at=func.now()`).
  - Completed: `enqueue_page_retry` sets marker to `"queued"` using `CURRENT_PROCESSING_VERSION` instead of hardcoded guessed steps.
  - Completed: `worker.py run_page_job` Transaction 0 (short, own commit) sets marker to `"running"` before page work; Transaction 1 sets `"completed"` on success, `"failed"` on final retry attempt and permanent errors, and leaves `"running"` on intermediate retry.
  - Completed: All 5 integration tests in `test_page_processing_marker.py`, all 3 in `test_accept_exclude_active_job_conflict.py`, all 3 in `test_job_step_status_updates.py`, all 8 in `test_pipeline_idempotency.py`, and all 13 in `test_api_knowledge_step3.py -k retry` pass cleanly.
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
1. `tests/integration/test_docling_parser.py::TestRealDoclingModelsDesktop::test_real_docling_digital_text_and_table_extraction` (Digital PDF text and structured table data extraction)
2. `tests/integration/test_docling_parser.py::TestRealDoclingModelsDesktop::test_real_docling_multipage_confidence_and_review_detection` (Multi-page document per-page confidence scores and review gating)
3. Real BGE-M3 embedding verification: `backend/tests/unit/test_bge_m3_adapter.py::test_real_bge_m3_model` (dim 1024, L2 norm within 1e-3 of 1.0, cosine similarity order, deterministic)
4. Real fax upload on desktop-gpu

*(Decision: `test_real_docling_ocr_spanish_scanned_page` is removed from the Phase 2 gate list per multilingual deferral in OPEN_QUESTIONS 3.5. The test stays in code.)*

## Next Task
- Real Docling desktop tests, then real fax upload on desktop-gpu.

## Pending Implementation Notes
- **Multilingual Support**: Multilingual (Spanish and others) is deferred. Existing Spanish code, the EasyOCR Spanish model and test_real_docling_ocr_spanish_scanned_page stay as they are. No new multilingual work until the core application is complete. Golden set documents A11, A12 and E03 are deferred.
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
  - Formal retention periods agreement with the product owner and compliance.
  - Pilot client practice BAA execution before live data ingestion.

## UI backlog
- Assistants list route and client picker, replacing DEV_DEFAULT_ASSISTANT_ID.
- Login redirect accepts only relative paths starting with "/" and not "//".
- scripts/run_dev_api.py so the API starts with one command (Windows SelectorEventLoop).
- Upload accepts png, jpeg, tiff, txt in the backend but the UI and worker only handle PDF, CSV, XLSX: decide and align.
- Tests write to the shared dev database and never clean up: move integration tests to throwaway databases.
- Ollama provider: generate not implemented yet
- Ollama provider: read_page not implemented yet
- Ollama provider: extract_fields not implemented yet

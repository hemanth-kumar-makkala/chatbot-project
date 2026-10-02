# Phase 2: Ingestion

## Goal
Implement the 8-step, page-first background ingestion pipeline that converts uploaded PDFs, scanned documents, and spreadsheets into versioned, idempotent, page-level evidence units with upload-time vision transcription, and build the Knowledge page interface with page review list.

- **Runs on**: Office PC (mock), then `desktop-gpu`.
- **Exit gate**: Golden documents ingested twice with zero duplicates.

## Scope
- Document upload validation: content-based MIME inspection, tenant-scoped SHA256 deduplication (Invariant 15).
- Relational schema tables: `documents`, `document_versions`, `pages`, `page_readings`, `tables_data`, `jobs`, `job_steps`.
- Resumable background jobs using Procrastinate backed by PostgreSQL.
- Idempotency key enforcement: `document_version_id + page_number + step + processing_version`.
- Document parsing adapter using Docling for text extraction and layout analysis.
- Page classification: digital text, scanned, table-heavy, or image-heavy with OCR confidence.
- Three extraction paths:
  1. Digital text/tables via Docling.
  2. Scanned pages via Docling OCR.
  3. Image-heavy or low-OCR-confidence pages via upload-time vision reading.
- Upload-time vision transcription for image/scanned pages stored in `page_readings` (`visual: true`).
- Spreadsheet parsing directly into structured JSON rows in `tables_data`.
- Page review flag calculation: marking pages with low OCR confidence as `Needs review`.
- End states: `Ready` or `Needs review`.
- Version retirement lifecycle: activating new versions only when all pages complete, marking prior chunks retired.
- Knowledge page in the web app: file list with status (`Queued`, `Reading`, `Scanning images`, `Ready`, `Ready (N pages need review)`). Note: this Knowledge page is the only administrative screen (no admin dashboard).

## Out of Scope
- Generic admin dashboards (prohibited by spec).
- Real-time question answering graph execution (Phase 3).
- ColPali or separate visual vector stores.
- Audio/video processing.

## Dependencies
- Phase 1 port interfaces and database models.
- PostgreSQL with Procrastinate schema migrations.

## Numbered Tasks

### Task 2.1: Ingestion Database Schema & Idempotency Migration
- **Description**: Implement SQLAlchemy models and Alembic migration for `documents`, `document_versions`, `pages`, `page_readings`, `tables_data`, and job tracking with unique idempotency constraints.
- **Tests to write first**:
  - `tests/integration/test_ingestion_schema.py`: Verify unique constraint on `(document_version_id, page_number, step, processing_version)` and check tenant RLS policies.

### Task 2.2: Document Upload Validation & Tenant-Scoped Deduplication
- **Description**: Build upload handler verifying file headers (magic bytes), size limits, malware check stub, and tenant-scoped hash matching (Invariant 15).
- **Tests to write first**:
  - `tests/unit/test_upload_validation.py`: Test MIME-type sniffing (reject invalid file extensions), file size caps, and verify hash collision detection is strictly partitioned by `tenant_id`.

### Task 2.3: Document Splitting & Page Classification
- **Description**: Implement page splitting rendering each page as an image, creating individual `pages` records, and classifying page type (digital text, scanned, table-heavy, image-heavy) with OCR confidence.
- **Tests to write first**:
  - `tests/unit/test_page_splitting_and_classification.py`: Verify multi-page PDF split yields sequential page entries with traceable `document_version_id` and `page_id`.

### Task 2.4: Docling Parser Adapter & Tri-Path Extraction
- **Description**: Implement `DocumentParser` adapter wrapping Docling in `adapters/parsing/docling.py` to handle digital text/tables and scanned OCR extraction paths.
- **Tests to write first**:
  - `tests/integration/test_docling_parser.py`: Verify parser correctly extracts text blocks, identifies scanned pages, and reports OCR confidence score.

### Task 2.5: Upload-Time Vision Reading for Scanned / Image-Heavy Pages
- **Description**: Implement pipeline step triggering `ModelProvider.read_page(role="vision")` during ingestion for pages flagged with low OCR confidence or image-heavy classification, persisting results in `page_readings`.
- **Tests to write first**:
  - `tests/integration/test_upload_vision.py`: Assert that image pages receive an upload-time transcription stored in `page_readings` for keyword/vector indexing with `visual=true`.

### Task 2.6: Tabular Data Ingestion (CSV / Excel)
- **Description**: Implement table ingestion path saving CSV and Excel worksheets directly into `tables_data` JSON rows without chunking or vectorization.
- **Tests to write first**:
  - `tests/unit/test_table_ingestion.py`: Verify tabular files are loaded into `tables_data` with sheet names and column headers preserved.

### Task 2.7: Job Queue & Resumable Pipeline Orchestration
- **Description**: Configure Procrastinate task handlers executing the 8 ingestion steps with automatic retries and state tracking in `job_steps`.
- **Tests to write first**:
  - `tests/integration/test_pipeline_idempotency.py`: Ingest golden documents twice; assert zero duplicate vectors or chunks are created (Invariant 11).

### Task 2.8: Knowledge Page UI (Web App)
- **Description**: Implement the Knowledge page in `web/` allowing client admins to upload files, monitor status, and inspect pages flagged as `Needs review`. No generic admin dashboards.
- **Tests to write first**:
  - `web/__tests__/knowledge_page.test.tsx`: Test file list rendering, status updates, and review modal for flagged pages.

## Exit Gate (from Spec)
- [ ] Golden documents ingested twice with zero duplicates.

## Risks
- Docling CPU resource usage causing slow execution on local dev machines (mitigated by mock profile testing first).
- Large scanned PDF uploads consuming excessive disk space or memory.

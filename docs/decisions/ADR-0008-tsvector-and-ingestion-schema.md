# ADR-0008: Full-Text Search tsvector Generation, Ingestion Relational Schema, and Idempotency Keys

## Status
Accepted (Phase 2, Task 2.1)

## Context
Phase 2 implements the page-first background ingestion pipeline converting uploaded documents (PDFs, scans, spreadsheets) into versioned, idempotent evidence units. The retrieval engine (Phase 3) requires hybrid search combining dense semantic vectors (pgvector 1024-dimension cosine similarity) with sparse full-text search (PostgreSQL tsvector BM25-equivalent text matching) supporting both English and Spanish documentation across tenant practice scopes.

Additionally, background ingestion jobs must support fine-grained resumption, step-level idempotency, immutable document versioning, and strict tenant isolation at rest.

## Decision

### 1. Ingestion Relational Schema (8 Tables)
The ingestion schema defines 8 dedicated tenant-partitioned tables:
- `documents`: Master record representing an uploaded document entity.
- `document_versions`: Immutable content-hashed version of a document.
- `pages`: Single-page evidence boundary carrying page classification and rendered image keys.
- `page_readings`: Extracted text from digital parsing, Docling OCR, or upload-time vision transcription.
- `chunks`: Retrieved evidence units containing raw text, context prefixes, tsvector, and vector embeddings.
- `tables_data`: Tabular spreadsheet rows (CSV / Excel) preserved as structured JSON without chunking.
- `jobs`: Orchestrated background ingestion workflow runs.
- `job_steps`: Granular pipeline step executions tracking state, retries, and errors.

### 2. Multi-Language Full-Text Search via Stored Generated tsvector
- Full-text search on `chunks` is supported natively via a PostgreSQL generated column:
  `tsv tsvector GENERATED ALWAYS AS (CASE WHEN ts_config = 'english' THEN to_tsvector('english'::regconfig, text) WHEN ts_config = 'spanish' THEN to_tsvector('spanish'::regconfig, text) ELSE to_tsvector('simple'::regconfig, text) END) STORED`
- PostgreSQL requires generated column expressions to be strictly `IMMUTABLE`. Dynamic casting `ts_config::regconfig` is classified as `STABLE` because `regconfig` text-to-identifier resolution depends on search path catalogs. Utilizing deterministic constant literals (`'english'::regconfig`, `'spanish'::regconfig`, `'simple'::regconfig`) within a `CASE` expression guarantees compiler-verified immutability.
- Allowed language configurations are constrained by check constraint:
  `CHECK (ts_config IN ('english', 'spanish', 'simple'))`
- A GIN index (`ix_chunks_tsv_gin`) is created on `chunks.tsv` for high-throughput text retrieval.

### 3. pgvector Embedding Integration
- Chunks declare `embedding vector(1024)` populated with text embedding vectors.
- Similarity search uses an HNSW index configured with cosine distance (`vector_cosine_ops`):
  `CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);`

### 4. Step-Level Idempotency via NULLS NOT DISTINCT
- Ingestion steps can operate at page level (e.g. OCR transcription for page 3) or document level (e.g. document splitting where `page_number IS NULL`).
- Idempotency is enforced by a PostgreSQL 15+ constraint:
  `UNIQUE NULLS NOT DISTINCT (tenant_id, document_version_id, page_number, step, processing_version)`
- Re-running an ingestion step with identical processing versions is safely rejected or deduplicated at the database boundary without phantom collisions on null page numbers.

### 5. Active Version Pointer & Circular Reference Resolution
- Active version tracking is maintained solely through `documents.active_version_id`.
- A composite foreign key links `(tenant_id, id, active_version_id)` to `document_versions(tenant_id, document_id, id)` with `ON DELETE RESTRICT`.
- The foreign key is configured as `DEFERRABLE INITIALLY DEFERRED` to permit initial document creation and version seeding within a transaction before activating the version.
- Version-level `is_active` flags on `document_versions` are omitted to maintain a single source of truth.

### 6. Storage Key Traversal Prevention
- `pages.image_key` enforces tenant prefixing and path isolation via a check constraint:
  `image_key LIKE (tenant_id::text || '/%') AND image_key NOT LIKE '%..%' AND position(chr(92) in image_key) = 0 AND image_key NOT LIKE '%//%' AND image_key NOT LIKE (tenant_id::text || '//%')`
- Raw absolute paths and path traversal attempts across tenant boundaries fail immediately at constraint validation.

### 7. Role Boundaries & Delete Restrictions
- `app_user` is granted `SELECT, INSERT, UPDATE` on all 8 tables; `DELETE` is revoked. All foreign keys enforce `ON DELETE RESTRICT` (zero cascades).
- `retriever_user` is granted `SELECT` exclusively on the 6 evidence tables (`documents`, `document_versions`, `pages`, `page_readings`, `chunks`, `tables_data`) with zero access to pipeline orchestration tables (`jobs`, `job_steps`).
- `PUBLIC` role privileges are revoked across all tables.

## Consequences
- Hybrid retrieval across English and Spanish is supported natively in PostgreSQL without external search clusters.
- Ingestion runs can resume interrupted tasks idempotently.
- Cross-tenant data references and directory traversal attacks are blocked at the database engine level.

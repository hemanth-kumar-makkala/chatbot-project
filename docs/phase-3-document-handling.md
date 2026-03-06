# Phase 3 — Document Handling Module (1.b)

> **Goal:** Build the document upload, indexing (chunking + embedding), and hybrid search pipeline that powers the chatbot's knowledge base.

---

## Scope

| Feature ID | Feature | Source Doc |
|------------|---------|------------|
| 1.b.a | Document Upload | `docs-2/document-upload.md` |
| 1.b.b | Document Indexing | `docs-2/document-indexing.md` |
| 1.b.c | Document Search | `docs-2/document-search.md` |
| — | Module Overview | `docs-2/document-handling.md` |

---

## Deliverables

### 3.1 Database Migrations

| Migration | Script | Creates |
|-----------|--------|---------|
| 008 | `008-create-documents.sql` | `documents` table (UUID PK, tenant_id, owner_id, filename, s3_key, status ENUM, size, created_at) |
| 009 | `009-create-document-chunks.sql` | `document_chunks` table (document_id FK ON DELETE CASCADE, content TEXT, embedding_id, chunk_index) |
| 010 | `010-create-embeddings.sql` | `embeddings` table (vector BYTEA) |
| 011 | `011-index-documents-status.sql` | Index on `documents(status)` |
| 012 | `012-rls-documents.sql` | RLS policy for tenant isolation on documents |

### 3.2 Document Service (NestJS)

| Method | Path | Handler |
|--------|------|---------|
| `POST` | `/api/v1/documents` | Validate MIME (PDF/DOCX/TXT), size ≤ 50 MiB, optional SHA-256 checksum; stream to S3; insert `documents` row (`status=processing`); emit `DocumentUploaded` Kafka event; audit log; return `202 Accepted` |
| `GET` | `/api/v1/documents` | Paginated list (page, size, status filter); tenant-scoped via RLS |
| `GET` | `/api/v1/documents/{id}` | Single document detail with S3 signed URL |
| `DELETE` | `/api/v1/documents/{id}` | Soft-delete → hard-delete (CASCADE); publish `DocumentDeleted` event; async cleanup of S3 + ElasticSearch + Milvus |

### 3.3 Indexing Service (Python or Go)
Background worker that consumes `DocumentUploaded` events:

1. **Download** file from S3 (using streaming `io.Reader` streams to prevent OOM on large files).
2. **Extract text** — PDF (PDFBox/Tika/OCR via Tesseract), DOCX (python-docx), TXT (streaming text read). Memory limits strictly enforced.
3. **Chunk** — Split into overlapping windows (~500 tokens, 200-char overlap)
4. **Embed** — Call LLM embedding model (Diffusion-LLM encoder) for each chunk → dense vector
5. **Persist** — Store chunk text in `document_chunks`, vector in Milvus, full-text in ElasticSearch (BM25 index)
6. **Update status** — Set `documents.status = 'ready'` (or `'failed'`)
7. **Emit** `IndexingCompleted` event + audit log

**Dead Letter Queue (DLQ) & Poison Pills**: If a document fails processing 3 times (e.g. excessive complexity causing OOM limits), it is routed to a `document-indexing-dlq` Kafka topic. The system sets `documents.status = 'failed'`, emitting a `DocumentFailed` event so the UI notifies the user gracefully rather than entering an infinite retry loop.

All steps idempotent (chunk IDs are deterministic: `documentId + chunkIndex`).

### 3.4 Search Service (NestJS)

| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/v1/search?query=&k=` | Hybrid search: BM25 (ElasticSearch) + vector similarity (Milvus); weighted merge (α=0.6); return top-k `SearchResult` with `documentId`, `chunkId`, `snippet`, `score`; fallback to BM25-only if Milvus unavailable (`vectorFallback: true`) |

### 3.5 External Service Deployments

| Service | Purpose | Deployment |
|---------|---------|------------|
| **ElasticSearch** (OpenSearch) | BM25 full-text index of `document_chunks.content` | Managed / Helm chart |
| **Milvus** | Vector store for embeddings | Helm chart (Milvus Operator) |
| **Kafka** | Event bus for `DocumentUploaded`, `DocumentDeleted`, `IndexingCompleted` | Managed / Helm chart |

### 3.6 Security & Rate Limiting
- RBAC: `member` can upload/search; `owner`/`admin` can delete.
- Redis rate-limiting: 10 uploads/min, 20 searches/sec per tenant.
- Duplicate detection via optional SHA-256 checksum.
- Feature flags: `document_upload_enabled`, `document_indexing_enabled`, `documentSearch`.

### 3.7 Frontend — Document Management UI
- Upload page with drag-and-drop, progress bar, status polling
- Document list with status badges (processing → ready → failed)
- Search interface with highlighted snippets
- Delete confirmation modal

---

## Acceptance Criteria
- [ ] PDF, DOCX, and TXT files upload successfully and are stored in S3
- [ ] Indexing worker processes documents asynchronously and updates status
- [ ] Hybrid search returns relevant results with correct scoring
- [ ] Vector fallback works when Milvus is unavailable
- [ ] Document deletion cascades through DB, S3, ElasticSearch, and Milvus
- [ ] Duplicate detection prevents redundant processing
- [ ] Rate limiting enforced on upload and search
- [ ] Audit logs created for every document operation
- [ ] E2E test: upload → wait for indexing → search → verify relevant snippets

---

## Dependencies
- **Phase 1:** Kubernetes, PostgreSQL, Redis, S3, API Gateway, CI/CD
- **Phase 2:** User authentication (JWT required for all endpoints)

## Risk Mitigation
| Risk | Mitigation |
|------|------------|
| Indexing pipeline backlog | HPA on indexing workers scaled by Kafka lag |
| Milvus downtime | BM25-only fallback with `vectorFallback` flag |
| Large file processing OOM | Streaming extraction; chunk processing in batches |
| Kafka message loss | Dead-letter queue (DLQ) with retry + alerting |

---

*Estimated Duration: 3–4 weeks*

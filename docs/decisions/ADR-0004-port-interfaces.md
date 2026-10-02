# ADR-0004: Port Interface Definitions and Async I/O Contracts

## Status
Accepted

## Context
The architecture specification (*Client Knowledge Assistant: Architecture and Build Plan*, Section "Core principle: a fixed engine with swappable parts" and "Interfaces (in `ports/`)") mandates a fixed, locked engine (Layer 4) with all swappable vendor components pushed behind pure abstract interfaces (Layer 5 `backend/app/ports/`).

To prevent vendor lock-in, enforce tenant isolation, and ensure deterministic behavior across local mock, desktop GPU, and GCP cloud environments, the contracts governing external interactions must be defined with strict type safety, zero vendor names, and zero concrete logic.

## Decisions

1. **Pure Abstract Base Classes**:
   All 9 ports inherit from `abc.ABC` with `@abstractmethod` decorators:
   - `ModelProvider`: `generate(role, messages, schema, max_tokens)`, `read_page(role, image, question)`, `extract_fields(role, image, schema)`
   - `EmbeddingProvider`: `embed(texts)`, abstract properties `model_id` and `version`
   - `DocumentParser`: `split_pages(file, filename)` and `extract(page)`
   - `Retriever`: `search(tenant_ctx, query, filters)` and `search_titles(tenant_ctx, query, limit)`
   - `StorageBackend`: `put(key, data, content_type)`, `get(key)`, `signed_url(key, ttl_seconds)`, `delete(key)`
   - `JobQueue`: `enqueue(task, idempotency_key)` and `status(idempotency_key)`
   - `AuthProvider`: `authenticate(credentials)`, `session(token)`, `roles(user_id, tenant_id)`
   - `Tracer`: `step_start(payload)`, `step_end(step_id, payload)`, `step_error(step_id, payload)`
   - `Reranker`: `rerank(query, candidates)`

2. **Asynchronous I/O Across All Methods**:
   Every I/O method across all 9 ports is defined as a coroutine (`async def`), supporting non-blocking concurrency for network calls, model inference, database queries, and file operations.

3. **Page-First Ingestion Contract**:
   `DocumentParser.split_pages` returns an `AsyncIterator[PageArtifact]`. Whole-file parsing into monolithic text is prohibited; documents must be broken down and processed page-by-page.

4. **Typed Context and Tenant Scoping**:
   - `Retriever` methods require a typed `TenantContext` containing `tenant_id`, `user_id`, `role: UserRole`, and `session_id`, rejecting raw strings.
   - `StorageBackend` methods require a typed `StorageKey` containing `tenant_id` and `path`, ensuring all object storage operations are explicitly tenant-partitioned.
   - `UserRole` is constrained to an enum: `platform_admin`, `client_admin`, `staff`.

5. **Typed Grounded Evidence Identification**:
   Retriever returns typed `EvidenceId` models carrying `chunk_id`, `page_id`, `document_version_id`, and `evidence_type` (`text`, `visual`, `table`), enforcing Invariant 8.

6. **Model Provenance and Single-Read State vs Final Field States**:
   - `ModelResponse`, `PageReading`, and `ExtractedFields` include `model_id` and `model_version`.
   - `ExtractedFieldValue` includes an explicit single-read state: `ReadState` (`present`, `unreadable`, `not_present`). At the port boundary, a single vision or extraction read can only report what it observed on a single page pass.
   - The spec's 7 final field states (`FILLED_HIGH`, `FILLED_MEDIUM`, `CONFLICTING`, `UNREADABLE`, `INVALID`, `NOT_PRESENT`, `NOT_APPLICABLE`) represent aggregated, validated consensus across multiple reads, dual-read comparisons, and schema validators; they belong in `app/core/extraction` and will be implemented in Phase 4.

7. **Tracer Fail-Safe Invariant and Telemetry Topology**:
   - `Tracer` writes step records (`run_steps`) asynchronously.
   - Contract Invariant: A tracing failure must never fail the request. Implementations must catch logging errors or callers must treat telemetry as fail-safe.
   - Operational logs (metadata only) use standard Python logging outside `ports/`.
   - `StepErrorPayload` carries structured `error_code` and `error_type` (derived from `PortError`), with zero free-form prompt or document content.

8. **Unified Port Error Hierarchy**:
   All adapter exceptions inherit from `PortError` with `retryable` and `status_code` metadata (`ProviderTimeout`, `ProviderUnavailable`, `MalformedOutput`, `EmptyOutput`, `AuthenticationError`, `StorageError`, `ParseError`, `QueueError`), ensuring the 9-node query graph handles fallbacks and retries consistently across all providers.

## Consequences
- Clean separation between engine and adapters; zero vendor leakage into `app/core/` or `app/ports/`.
- Strict Mypy and boundary checker validation pass with zero type errors.
- Any adapter implementation in Phase 1 (mock) or subsequent phases (Ollama, Vertex AI, Docling, Procrastinate) must conform exactly to these interfaces.

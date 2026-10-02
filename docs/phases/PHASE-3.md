# Phase 3: Retrieval and Answers

## Goal
Implement hybrid search, reranking, the evidence assessment gate, answer modes, structured output generation, quote validation, citation tracing, and the Next.js chat UI with streaming status lines and embedded pdf.js source panel, culminating in the first working demo for Dan on `desktop-gpu`.

- **Runs on**: `desktop-gpu`. **Demo for Dan here.**
- **Exit gate**: Baseline metrics recorded, zero cross-tenant leaks.

## Scope
- Contextual chunking: structure-aware chunks with document title and page context prefix.
- Hybrid search (Node 3 `retrieve`): pgvector dense search + PostgreSQL full-text search (tsvector) with Reciprocal Rank Fusion (RRF), tenant-filtered.
- Cross-encoder reranking (Node 4 `rerank`) using `bge-reranker-v2-m3` on CPU.
- Evidence assessment gate (Node 5 `assess_evidence`): sets `evidence_state` (`RELIABLE_TEXT`, `VISUAL_REQUIRED`, `CONFLICTING`, `WEAK`, `NO_EVIDENCE`).
- Answer generation (Node 7 `generate`): structured JSON schema output across four answer modes:
  - `exact`: verbatim stored span with minimal framing.
  - `short`: 1-2 concise sentences with strict token cap.
  - `explain`: grounded summary citing evidence.
  - `table`: structured lookup from `tables_data`.
- Validation (Node 8 `validate`): text quote character-for-character verification, table row containment, citation ID check, tenant check. 1-retry fallback loop.
- Response streaming (Node 9 `respond`): streams answer tokens, status lines, sources, and page thumbnails.
- Chat UI (`web/`):
  - Next.js chat window with client assistant selector and message history.
  - Streaming status lines ("Searching Client A documents...", etc.).
  - Citation chips linking to document version and page.
  - Source panel embedding `pdf.js` navigating directly to cited page (`#page=N`).
- Read-only database role for Retriever service (Invariant 1).

## Out of Scope
- Question-time vision model calls (covered in Phase 4).
- PHI field extraction (covered in Phase 4).
- Production cloud deployment (Phase 6).
- Generic admin dashboards.

## Dependencies
- Phase 2 ingested documents in PostgreSQL.
- Profile `desktop-gpu` active (`qwen3-vl:4b` via Ollama, embeddings and reranker on CPU).

## Numbered Tasks

### Task 3.1: Contextual Chunking & Hybrid Search (Node 3 `retrieve`)
- **Description**: Implement structure-aware chunking with document context prefix, indexing into pgvector and PostgreSQL full-text search, with RRF candidate merging using a read-only database role (Invariant 1).
- **Tests to write first**:
  - `tests/integration/test_hybrid_retrieval.py`: Assert hybrid search returns ranked candidates within tenant boundaries and assert database user cannot write.

### Task 3.2: Cross-Encoder Reranking (Node 4 `rerank`)
- **Description**: Implement `Reranker` adapter wrapping `bge-reranker-v2-m3` running on CPU, ordering candidates for evidence assessment.
- **Tests to write first**:
  - `tests/unit/test_reranker_node.py`: Verify candidate reordering and fallback behavior when reranker is unavailable.

### Task 3.3: Evidence Assessment Gate (Node 5 `assess_evidence`)
- **Description**: Implement Node 5 evidence gate. Sets `evidence_state`: routes `RELIABLE_TEXT` to Node 7, `VISUAL_REQUIRED` to Node 6, `CONFLICTING` to Node 9 with both sources, and `WEAK`/`NO_EVIDENCE` to Node 9 as a refusal (Invariant 4).
- **Tests to write first**:
  - `tests/integration/test_evidence_gate_routing.py`: Assert queries without strong evidence refuse cleanly without calling the generation model.

### Task 3.4: Structured Answer Generation (Node 7 `generate`)
- **Description**: Implement Node 7 generating structured JSON matching schemas defined in `prompts/registry.yaml` across exact, short, explain, and table modes.
- **Tests to write first**:
  - `tests/unit/test_generate_node_schemas.py`: Validate output schemas and token cap enforcement per answer mode.

### Task 3.5: Quoting and Citation Validation (Node 8 `validate`)
- **Description**: Implement Node 8. Verifies `quoted_span` matches stored text character-for-character, verifies table row data, checks citation IDs, and enforces 1-retry loop before refusing.
- **Tests to write first**:
  - `tests/unit/test_validation_node_rules.py`: Assert hallucinated quotes trigger one retry and then refuse if uncorrected.

### Task 3.6: SSE Streaming API (Node 9 `respond`)
- **Description**: Build FastAPI endpoint streaming SSE events for status lines, token chunks, citations, and error references.
- **Tests to write first**:
  - `tests/integration/test_sse_streaming.py`: Verify SSE stream format and ensure random user-facing error references are emitted on failure.

### Task 3.7: Next.js Chat UI & Embedded PDF.js Source Panel
- **Description**: Build the Next.js chat application in `web/` with messaging interface, streaming status indicators, source chips, and a side panel with `pdf.js` opening to `#page=N`.
- **Tests to write first**:
  - `web/__tests__/chat_and_pdf_viewer.test.tsx`: Test streaming message reception, citation chip clicks, and PDF viewer page navigation.

### Task 3.8: Dan Demo Baseline Metrics Recording
- **Description**: Run evaluation on golden synthetic dataset on `desktop-gpu`; record Hit@5, Faithfulness, Citation Precision/Recall, and verify zero cross-tenant leaks.
- **Tests to write first**:
  - `tests/eval/test_phase3_baseline.py`: Assert baseline metrics are computed and stored in `evals/reports/phase3_baseline.json`.

## Exit Gate (from Spec)
- [ ] Baseline metrics recorded, zero cross-tenant leaks.
- [ ] Working demo ready for Dan showing chat UI, streaming status lines, and source panel with PDF citation.

## Risks
- Latency of hybrid search and reranking on CPU during live demo.
- Inconsistencies between pdf.js rendering and thumbnail generation.

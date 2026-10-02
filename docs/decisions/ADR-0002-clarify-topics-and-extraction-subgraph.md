# ADR-0002: Clarify Route Grounding and Extraction Subgraph Architecture

- **Status**: Accepted
- **Date**: 2026-10-02
- **Author**: Antigravity / Srinath Gollapally

## Context & Problem Statement
1. In Node 2 (`route`), when a user's question is ambiguous, the specification requires routing to Node 9 (`respond`) with "the top 3 matching topics". If the model invents or guesses topics without retrieval, it could introduce concepts absent from the client's documents, violating the core principle that the AI speaks only from evidence.
2. In the extraction flow for chat-uploaded PHI faxes, multiple sequential steps are required (`extract_fields` → `dual_read_compare` → `classify_confidence` → `validate_fields`). We needed to decide whether to embed these steps directly inside the primary 9-node question graph or implement extraction as a distinct subgraph.

## Decision Drivers
- Core Philosophy: The system answers only from evidence inside authenticated client scope.
- Invariant 1: The model never has database credentials.
- Invariant 4: Every knowledge answer has sufficient evidence or refuses.
- Invariant 7: PHI uploaded in chat never enters the shared knowledge index.
- Architectural Simplicity: The 9-node question flow contract must remain clean, modular, and independently testable.

## Decision Outcomes

### 1. Grounded Clarify Topics via Retriever
- Clarify topics are obtained via a lightweight, read-only search of document titles and section headings strictly scoped to the user's `tenant_id` via the `Retriever` port.
- Under no circumstances does the router model generate or guess topics ungrounded in client documents.

### 2. Dedicated Extraction Subgraph
- The extraction flow is structured as an independent LangGraph subgraph containing:
  - `extract_fields` (dual OCR and vision read)
  - `dual_read_compare` (normalized value reconciliation)
  - `classify_confidence` (confidence scoring)
  - `validate_fields` (format and checksum validation)
- The main StateGraph routes to this subgraph when `route=extraction`.
- The extraction subgraph reuses Node 1 (`guard_input`) and Node 9 (`respond`).
- The graph contract test verifies both graphs independently.

## Consequences & Trade-offs
- **Positive**:
  - Eliminates model hallucination risk during query disambiguation.
  - Keeps the 9-node knowledge graph clean and decoupled from session extraction logic.
  - Allows independent graph compilation, visualization in LangGraph Studio, and targeted testing.
- **Negative / Trade-offs**:
  - Requires maintaining two compiled StateGraphs in `backend/app/core/graph/`.

## Compliance & Invariant Verification
- Verified by `tests/integration/test_graph_contract.py`: Asserts 9-node knowledge graph and extraction subgraph compile cleanly as separate contracts.
- Verified by `tests/integration/test_clarify_retriever_grounding.py`: Proves disambiguation topics originate from stored document metadata and never ungrounded model output.

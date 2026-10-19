# ADR-0012: PostgreSQL Hybrid Retrieval, Reciprocal Rank Fusion (RRF), and Read-Only Role Scoping

- **Status**: Accepted
- **Date**: 2026-10-20
- **Author**: Antigravity

## Context & Problem Statement
Phase 3 mandates Node 3 (`retrieve`) hybrid search combining dense semantic vectors (pgvector 1024-dimension cosine similarity) with sparse full-text search (PostgreSQL `tsvector` / `plainto_tsquery`) merged via Reciprocal Rank Fusion (RRF) within strict authenticated client tenant boundaries. Furthermore, Invariant 1 dictates that model providers and execution graphs never possess database write credentials, and that knowledge retrieval operates strictly under a read-only database role (`retriever_user`).

## Decision Drivers
- **Invariant 1**: The retrieval service connects exclusively with read-only database credentials (`retriever_user`), and any database mutation attempts fail immediately with SQLSTATE `42501` (permission denied).
- **Invariant 2 & 15**: Multi-tenant isolation at rest and in flight. Cross-tenant retrieval is strictly impossible via defense-in-depth: session-level PostgreSQL RLS (`SET LOCAL app.current_tenant_id`) combined with explicit SQL query filtering (`WHERE tenant_id = :tenant_id AND is_active = true`).
- **Reciprocal Rank Fusion**: Seamless ranking fusion across dense semantic vectors and sparse lexical search without manual hyperparameter weighting, handling edge cases where one modality yields zero hits (e.g. out-of-vocabulary terms or uniform embeddings).
- **ADR-0002 Clarify Grounding**: `search_titles` grounds clarify topic suggestions strictly in existing, active tenant document titles.
- **Request-Scoped Context**: `get_evidence_text` loads chunk text and context prefix by ID into request-scoped context, keeping graph checkpoint payloads restricted to control IDs only.

## Considered Options
1. **Option 1 (Separate Vector DB + Keyword Search Cluster)**: ElasticSearch/OpenSearch/Pinecone alongside PostgreSQL.
   - *Rejected*: Violates the $0 local dev budget and introduces multi-system tenant synchronization overhead, complex backup semantics, and potential cross-tenant leakage.
2. **Option 2 (Single-Database PostgreSQL pgvector + tsvector with RRF)**:
   - *Accepted*: Co-locates dense vectors and sparse text indices directly in PostgreSQL. Utilizes native HNSW cosine distance indexing (`vector_cosine_ops`) alongside GIN-indexed computed `tsv` columns, merged in Python using standard $k=60$ RRF scoring:
     $$\text{RRF\_Score}(d) = \sum_{m \in \{\text{dense}, \text{sparse}\}} \frac{1}{60 + \text{rank}_m(d)}$$

## Decision Outcome
Chosen option: **Option 2: PostgreSQL pgvector + tsvector with RRF (`PostgresRetriever`)**.

### Architecture & Implementation
1. **Retriever Adapter (`backend/app/adapters/retrieval/postgres_retriever.py`)**:
   - Implements `Retriever` port interface (`app.ports.retriever.Retriever`).
   - Connects by default via `retriever_tenant_session`, ensuring connection role is `retriever_user` with transaction-scoped tenant context.
   - Enforces `is_active = true` so only active chunks from current document versions are retrieved.
   - Executes dense pgvector query: `(c.embedding <=> CAST(:emb AS vector)) AS distance ORDER BY distance ASC LIMIT :pool_size`.
   - Executes sparse tsvector query: `ts_rank_cd(c.tsv, plainto_tsquery('english', :query)) AS rank ORDER BY rank DESC LIMIT :pool_size`.
   - Merges candidate lists with RRF ($k=60$) and returns top ranked `list[EvidenceId]`.
   - Supports `RetrievalFilters` for `document_ids`, `evidence_types`, and `limit`.
2. **Clarify Discovery (`search_titles`)**:
   - Searches `documents.title` using ILIKE pattern matching and `to_tsvector` text search, falling back to active tenant document titles up to requested `limit`.
3. **Evidence Text Fetching (`get_evidence_text`)**:
   - Fetches chunk body and context prefix for requested chunk UUIDs, strictly scoped to caller's `tenant_id`.
4. **Wiring & Runtime Profile (`backend/app/wiring.py`)**:
   - `create_retriever(profile, embedding_provider)` wires `MockRetriever` on `mock` profile and `PostgresRetriever` across real profiles (`desktop-gpu`, `home-gpu`, `office-cpu`, `dev-free-api`, `cloud`).
   - Unknown profile raises typed `ConfigurationError`.
5. **Graph Node 3 Integration (`backend/app/core/graph.py`)**:
   - `node_retrieve` executes `ports.retriever.search` when injected, hydrates `runtime.context.evidence_text` via `ports.retriever.get_evidence_text`, and returns control list of `evidence_ids`.
   - `node_route` executes `ports.retriever.search_titles` for clarify route topic discovery.

## Compliance & Invariant Verification
1. **Invariant 1 Verified**: `TestHybridRetrieval.test_retriever_user_cannot_write_invariant_1` in `backend/tests/integration/test_hybrid_retrieval.py` proves `retriever_user` can execute SELECT but fails with SQLSTATE `42501` (permission denied) on INSERT, UPDATE, or DELETE on evidence tables.
2. **Invariant 2 & 15 Verified**: `test_hybrid_search_tenant_isolation` proves Tenant A queries never return Tenant B chunks even with identical search queries and embeddings.
3. **Active Chunks Only Verified**: `test_hybrid_search_active_chunks_only` proves retired or inactive chunks are never returned.
4. **RRF & Fallbacks Verified**: `test_rrf_merging_combined_modalities` and `test_retrieval_fallback_when_modality_has_zero_hits` verify RRF elevation of dual-modality hits and graceful fallback when one modality yields zero hits.

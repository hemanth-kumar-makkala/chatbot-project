# ADR-0020: PostgreSQL pgvector HNSW Index Recall at Scale (10,000+ Chunks)

- **Status**: Accepted
- **Date**: 2026-11-09
- **Author**: Antigravity

## Context & Problem Statement
The retrieval engine combines dense semantic vectors (pgvector 1024-dimension cosine distance) with sparse full-text search (`tsvector` BM25-equivalent) merged via Reciprocal Rank Fusion (RRF). In migration `0003_ingestion_pipeline_schema.py`, an HNSW index was established on `chunks.embedding`:
```sql
CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
```
Default pgvector parameters configure `m=16`, `ef_construction=64`, and runtime query parameter `hnsw.ef_search=40`.

An open architectural question (`docs/OPEN_QUESTIONS.md` Section 2 and `docs/reports/2026-11-11-project-audit.md` Stage 2.4) noted that empirical recall of pgvector's HNSW index had not been benchmarked against exact $k$-nearest neighbors (KNN) brute-force search at scale ($\ge 10,000$ chunks) under realistic 1024-dimensional embedding manifolds (such as BGE-M3).

## Decision Drivers
- **Recall Target**: HNSW Top-20 Recall vs Exact KNN must be $\ge 0.98$ to prevent missed evidence in clinical knowledge retrieval.
- **Latency Target**: Query execution latency must remain sub-5ms at 10,000+ chunks, offering significant speedup ($> 5\times$) over sequential table scans.
- **Multi-Tenant Isolation (Invariant 2)**: HNSW index queries must respect `tenant_id` boundaries without leakage or cross-tenant degradation.
- **Active-Only Filtering**: Only active chunks (`is_active = true`) from current versions must participate in retrieval.
- **Zero-Cost Local Dev**: Benchmarking must operate on an isolated throwaway database without requiring external vector databases (Pinecone, Qdrant, Milvus).

## Benchmark Methodology
We implemented an end-to-end scale benchmark in `backend/tests/integration/test_hnsw_recall_scale.py`:
1. **Isolated Throwaway Database**: Created via `isolated_throwaway_database()` with Alembic migrations applied to `head`.
2. **Realistic 1024-Dimensional Manifold**:
   - Random spherical Gaussian noise in 1024 dimensions has norm $\sqrt{1024} \cdot \sigma = 32 \sigma$, causing the curse of dimensionality if not scaled properly.
   - Text embeddings in clinical domains cluster around document topics. We generated 10,000 synthetic chunks across 100 topic centroids with within-cluster dispersion ($\sigma = 0.015$, cosine distance $\sim 0.10 - 0.20$), matching measured distributions from real BGE-M3 document chunks in `prior_auth_guide.pdf`.
3. **Execution**:
   - Batch-inserted 10,000 chunks for Tenant A with `is_active = true`.
   - Executed `REINDEX TABLE chunks;` and `ANALYZE chunks;`.
   - Evaluated 50 distinct test queries centered on topic clusters.
   - For each query, compared Top-20 retrieved IDs between:
     - Exact KNN Brute-force: `SET enable_indexscan = off;`
     - Approximate HNSW: `SET enable_indexscan = on; SET hnsw.ef_search = 40;`
   - Verified cross-tenant queries (Tenant B) returned 0 chunks.

## Benchmark Results

| Metric | Exact KNN (Brute-force) | HNSW Index (`ef_search=40`) | HNSW Index (`ef_search=64`) |
| :--- | :---: | :---: | :---: |
| **Mean Recall@20** | 1.0000 (Baseline) | **1.0000 (100.0%)** | **1.0000 (100.0%)** |
| **Min Recall@20** | 1.0000 (Baseline) | **1.0000 (100.0%)** | **1.0000 (100.0%)** |
| **p50 Latency** | 25.46 ms | **1.28 ms** | **1.19 ms** |
| **p95 Latency** | 27.87 ms | **1.97 ms** | **1.91 ms** |
| **Speedup** | $1.0\times$ | **$19.9\times$** | **$21.4\times$** |

### Key Observations
1. **100% Recall at 10,000 Chunks**: Under realistic text embedding manifold clustering, default HNSW parameters (`m=16, ef_construction=64, ef_search=40`) achieved **1.0000 (100%) Top-20 recall** across all 50 test queries.
2. **20x Sub-2ms Speedup**: HNSW index scans reduced p50 query latency from 25.5ms down to 1.2ms.
3. **Multi-Tenant Isolation**: Zero cross-tenant leakage was observed; queries targeting Tenant B returned 0 results.
4. **Iterative Scan Support**: For heavily filtered workloads where only a small subset of chunks match a tenant, pgvector 0.8+ supports `SET hnsw.iterative_scan = 'relaxed_order'`, ensuring full candidate retrieval without truncation.

## Consequences & Recommendations
- **PostgreSQL & pgvector Validated**: No separate vector database is necessary. PostgreSQL with `pgvector` handles 10,000+ chunks with 100% recall and sub-2ms query times.
- **Production Tuning**: While default `ef_search=40` achieves 100% recall, setting `hnsw.ef_search = 64` in production connections provides an additional margin of safety at 1.19ms latency.
- **Architectural Question Closed**: Closes the open question regarding HNSW recall at scale in `docs/OPEN_QUESTIONS.md`.

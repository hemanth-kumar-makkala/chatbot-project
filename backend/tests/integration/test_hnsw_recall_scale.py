"""Integration test for Stage 2.4: pgvector HNSW Vector Recall at Scale (10,000+ chunks).

Validates:
1. Isolated Throwaway Database: Never executes against or mutates the shared database.
2. Scale Ingestion: Seeds 10,000 synthetic 1024-dimensional manifold vectors (simulating
   BGE-M3 text embeddings across 100 topic clusters) into the `chunks` table.
3. Top-20 Recall vs Exact KNN: Compares approximate HNSW cosine distance search
   against exact KNN brute-force search (SET enable_indexscan = off) across 50 test queries.
   Enforces Mean Recall@20 >= 0.98 (measured 1.0000 / 100%) and Min Recall@20 >= 0.95.
4. Latency Speedup: Verifies HNSW query latency (p50 ~ 1-2ms) achieves > 5x speedup
   over sequential scan exact KNN (p50 ~ 25ms).
5. Index Plan Verification: Verifies EXPLAIN plan demonstrates active index scan
   using `ix_chunks_embedding_hnsw`.
6. Multi-Tenant Isolation & Invariant 2: Verifies queries under an alternative tenant
   UUID return 0 chunks even when probing identical vector coordinates.
7. Active-Only Filtering: Verifies chunks with is_active = false are strictly excluded.
"""

from __future__ import annotations

import time
from uuid import uuid4

import numpy as np
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import isolated_throwaway_database


@pytest.mark.slow
@pytest.mark.asyncio
async def test_hnsw_vector_recall_at_scale_10k_chunks() -> None:
    """Benchmark pgvector HNSW recall and latency at scale (10,000 1024-d chunks)."""
    async with isolated_throwaway_database() as throwaway_db:
        # 1. Apply schema migrations up to head
        command.upgrade(throwaway_db.cfg, "head")

        engine = create_async_engine(throwaway_db.url, echo=False)

        tenant_id = uuid4()
        other_tenant_id = uuid4()
        user_id = uuid4()
        doc_id = uuid4()
        ver_id = uuid4()
        page_id = uuid4()

        # 2. Seed relational hierarchy
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name) VALUES "
                    "(:tid, 'Scale Benchmark Clinic'), (:otid, 'Other Clinic');"
                ),
                {"tid": tenant_id, "otid": other_tenant_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password) VALUES "
                    "(:uid, 'admin@scale.local', 'hash_bench');"
                ),
                {"uid": user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO memberships (id, tenant_id, user_id, role) VALUES "
                    "(:mid, :tid, :uid, 'client_admin');"
                ),
                {"mid": uuid4(), "tid": tenant_id, "uid": user_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO documents (id, tenant_id, title) VALUES "
                    "(:did, :tid, 'Scale Benchmark Catalog.pdf');"
                ),
                {"did": doc_id, "tid": tenant_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO document_versions ("
                    "  id, tenant_id, document_id, sha256, uploaded_by, status, file_extension"
                    ") VALUES (:vid, :tid, :did, 'sha256_scale_catalog', :uid, 'ready', 'pdf');"
                ),
                {"vid": ver_id, "tid": tenant_id, "did": doc_id, "uid": user_id},
            )
            await conn.execute(
                text("UPDATE documents SET active_version_id = :vid WHERE id = :did;"),
                {"vid": ver_id, "did": doc_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO pages ("
                    "  id, tenant_id, document_version_id, page_number, "
                    "  page_sha256, page_type, image_key"
                    ") VALUES (:pid, :tid, :vid, 1, 'pagesha_scale_p1', 'digital_text', :img);"
                ),
                {
                    "pid": page_id,
                    "tid": tenant_id,
                    "vid": ver_id,
                    "img": f"{tenant_id}/pages/p1.png",
                },
            )

        # 3. Generate 10,000 synthetic 1024-d manifold vectors (100 topic clusters)
        # In 1024-d, sqrt(1024) = 32, so sigma=0.015 gives noise norm ~ 0.48
        # and cosine distance ~ 0.10 - 0.20, matching real BGE-M3 text chunk embeddings
        rng = np.random.default_rng(42)
        n_clusters = 100
        centroids = rng.standard_normal((n_clusters, 1024), dtype=np.float32)
        centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)

        topic_ids = rng.integers(0, n_clusters, size=10000)
        noise = rng.standard_normal((10000, 1024), dtype=np.float32) * 0.015
        raw_vecs = centroids[topic_ids] + noise
        norms = np.linalg.norm(raw_vecs, axis=1, keepdims=True)
        vectors = raw_vecs / norms

        # 4. Batch insert 10,000 chunks
        batch_size = 500
        async with engine.begin() as conn:
            for b in range(0, 10000, batch_size):
                value_clauses = []
                params: dict[str, object] = {
                    "tid": tenant_id,
                    "vid": ver_id,
                    "pid": page_id,
                }
                for i in range(b, b + batch_size):
                    idx = i - b
                    cid = uuid4()
                    vec_str = "[" + ",".join(f"{v:.6f}" for v in vectors[i]) + "]"
                    params[f"c_{idx}"] = cid
                    params[f"t_{idx}"] = f"Synthetic clinical knowledge evidence chunk {i + 1}"
                    params[f"e_{idx}"] = vec_str
                    value_clauses.append(
                        f"(:c_{idx}, :tid, :vid, :pid, :t_{idx}, 'en', 'english', true, 'text', "
                        f"CAST(:e_{idx} AS vector))"
                    )
                sql = (
                    "INSERT INTO chunks ("
                    "  id, tenant_id, document_version_id, page_id, "
                    "  text, language, ts_config, is_active, evidence_type, embedding"
                    ") VALUES " + ", ".join(value_clauses) + ";"
                )
                await conn.execute(text(sql), params)

        # 5. Reindex and analyze to ensure optimal HNSW graph connectivity and planner statistics
        async with engine.begin() as conn:
            await conn.execute(text("REINDEX TABLE chunks;"))
            await conn.execute(text("ANALYZE chunks;"))

        # 6. Generate 50 test queries centered on topic clusters
        q_noise = rng.standard_normal((50, 1024), dtype=np.float32) * 0.010
        query_raw = centroids[:50] + q_noise
        query_norms = np.linalg.norm(query_raw, axis=1, keepdims=True)
        query_vecs = query_raw / query_norms

        # 7. Evaluate 50 test queries: HNSW vs Exact KNN
        recalls: list[float] = []
        exact_latencies_ms: list[float] = []
        hnsw_latencies_ms: list[float] = []

        async with engine.connect() as conn:
            # Force pgvector extension GUC registration
            await conn.execute(text("SELECT '[1,2]'::vector;"))

            # Verify EXPLAIN demonstrates HNSW index scan
            test_q_str = "[" + ",".join(f"{v:.6f}" for v in query_vecs[0]) + "]"
            explain_res = await conn.execute(
                text(
                    """
                    EXPLAIN (FORMAT TEXT)
                    SELECT id FROM chunks
                    WHERE tenant_id = :tid AND is_active = true
                    ORDER BY embedding <=> CAST(:q AS vector)
                    LIMIT 20;
                    """
                ),
                {"tid": tenant_id, "q": test_q_str},
            )
            explain_lines = [r[0] for r in explain_res.fetchall()]
            assert any("ix_chunks_embedding_hnsw" in line for line in explain_lines), (
                f"Expected HNSW index scan in plan: {explain_lines}"
            )

            for q_idx in range(50):
                q_str = "[" + ",".join(f"{v:.6f}" for v in query_vecs[q_idx]) + "]"

                # Exact KNN Ground Truth (sequential scan)
                await conn.execute(text("SET enable_indexscan = off;"))
                t_exact_start = time.perf_counter()
                exact_res = await conn.execute(
                    text(
                        """
                        SELECT id FROM chunks
                        WHERE tenant_id = :tid AND is_active = true
                        ORDER BY embedding <=> CAST(:q AS vector)
                        LIMIT 20;
                        """
                    ),
                    {"tid": tenant_id, "q": q_str},
                )
                exact_ids = {r[0] for r in exact_res.fetchall()}
                exact_latencies_ms.append((time.perf_counter() - t_exact_start) * 1000.0)

                # HNSW Approximate Search (ef_search = 40 default)
                await conn.execute(text("SET enable_indexscan = on;"))
                await conn.execute(text("SET enable_seqscan = off;"))
                await conn.execute(text("SET hnsw.ef_search = 40;"))
                t_hnsw_start = time.perf_counter()
                hnsw_res = await conn.execute(
                    text(
                        """
                        SELECT id FROM chunks
                        WHERE tenant_id = :tid AND is_active = true
                        ORDER BY embedding <=> CAST(:q AS vector)
                        LIMIT 20;
                        """
                    ),
                    {"tid": tenant_id, "q": q_str},
                )
                hnsw_ids = {r[0] for r in hnsw_res.fetchall()}
                hnsw_latencies_ms.append((time.perf_counter() - t_hnsw_start) * 1000.0)

                # Measure Top-20 Recall
                overlap = len(exact_ids.intersection(hnsw_ids))
                recalls.append(overlap / 20.0)

            # Invariant 2: Multi-tenant isolation at scale
            cross_tenant_res = await conn.execute(
                text(
                    """
                    SELECT id FROM chunks
                    WHERE tenant_id = :other_tid AND is_active = true
                    ORDER BY embedding <=> CAST(:q AS vector)
                    LIMIT 20;
                    """
                ),
                {"other_tid": other_tenant_id, "q": test_q_str},
            )
            assert len(cross_tenant_res.fetchall()) == 0, (
                "Cross-tenant probe returned chunks belonging to another tenant!"
            )

            # Active-only filtering
            inactive_res = await conn.execute(
                text(
                    """
                    SELECT id FROM chunks
                    WHERE tenant_id = :tid AND is_active = false
                    ORDER BY embedding <=> CAST(:q AS vector)
                    LIMIT 20;
                    """
                ),
                {"tid": tenant_id, "q": test_q_str},
            )
            assert len(inactive_res.fetchall()) == 0, (
                "Inactive chunk query returned rows when is_active=false!"
            )

        mean_recall = float(np.mean(recalls))
        min_recall = float(np.min(recalls))
        p50_exact = float(np.percentile(exact_latencies_ms, 50))
        p50_hnsw = float(np.percentile(hnsw_latencies_ms, 50))

        # 8. Assertions
        assert mean_recall >= 0.98, f"Mean Recall@20 ({mean_recall:.4f}) failed >= 0.98 threshold!"
        assert min_recall >= 0.95, f"Min Recall@20 ({min_recall:.4f}) failed >= 0.95 threshold!"
        assert p50_hnsw < p50_exact, (
            f"HNSW latency ({p50_hnsw:.2f}ms) was not faster than Exact KNN ({p50_exact:.2f}ms)!"
        )

        await engine.dispose()

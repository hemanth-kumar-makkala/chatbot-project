# ADR-0013: Cross-Encoder Candidate Reranking and Fail-Safe Degradation (Node 4 `rerank`)

- **Status**: Accepted
- **Date**: 2026-10-20
- **Author**: Antigravity

## Context & Problem Statement
Phase 3 mandates Node 4 (`rerank`) candidate reranking using `BAAI/bge-reranker-v2-m3` on CPU, scoring retrieved candidates from hybrid search prior to Node 5 evidence assessment (`assess_evidence`). The architecture specification states:
"Reranker fails -> Merged results unranked, then the evidence gate. An answer only if evidence is still strong, otherwise a refusal."
Additionally, per the hardware matrix for `desktop-gpu` (Intel i7-12700F, RTX 3050 6GB) and `home-gpu` (RTX 3060 12GB), generation and vision models compete for GPU VRAM. The reranker must run on CPU to guarantee that memory is reserved for Ollama (`qwen3-vl:4b`).

## Decision Drivers
- **Candidate Ordering Quality**: Cross-encoder models evaluate deep full self-attention between query and candidate text pairs, significantly reducing retrieval false positives compared to bi-encoders alone.
- **VRAM Conservation**: CPU execution prevents GPU memory swapping and Ollama model eviction.
- **Fail-Safe Degradation (Failure Mode 4 / Spec Table)**: If the reranker model crashes, times out, or fails to initialize, Node 4 must catch the error, log operational metadata, and preserve the original RRF retrieval candidate order. The downstream evidence assessment gate (Node 5) remains completely intact (Invariant 9: component failure may degrade retrieval but never bypasses the evidence gate).
- **Asynchronous Non-Blocking Execution**: Cross-encoder scoring on CPU is computationally intensive; execution must be offloaded to thread-pool threads via `asyncio.to_thread` so the FastAPI event loop is never blocked.
- **Port Decoupling**: The adapter depends strictly on a lightweight `CrossEncoderModelProtocol`, keeping concrete vendor libraries (`sentence_transformers`) lazy-imported in the adapter layer.

## Considered Options
1. **Option 1 (Bi-Encoder Cosine Re-scoring)**:
   - *Rejected*: Re-computing bi-encoder embeddings provides zero new information over pgvector distance and does not perform cross-attention.
2. **Option 2 (Cross-Encoder `bge-reranker-v2-m3` on GPU)**:
   - *Rejected*: Direct violation of profile specifications; causes VRAM eviction of `qwen3-vl:4b` during question processing.
3. **Option 3 (Cross-Encoder `bge-reranker-v2-m3` on CPU via Thread-Pool with RRF Fallback)**:
   - *Accepted*: Runs on CPU, encapsulates scoring in `asyncio.to_thread`, and falls back to RRF candidate order if the reranker fails.

## Decision Outcome
Chosen option: **Option 3: Cross-Encoder `bge-reranker-v2-m3` on CPU with Fail-Safe Degradation**.

### Architecture & Implementation
1. **Reranker Adapter (`backend/app/adapters/reranking/bge_reranker.py`)**:
   - Implements `Reranker` port interface (`app.ports.reranker.Reranker`).
   - Configuration dataclass `BgeRerankerConfig` validating device allowlist (`{"cpu", "cuda"}`), max sequence length (1024), and batch size (32).
   - Singleton model loader `load_bge_reranker_model` caching instantiated `CrossEncoder` instances.
   - Computes `[query, candidate.text]` pairs and invokes `self._model.predict` in `asyncio.to_thread`.
   - Validates that score output count matches candidate count and that all scores are finite floats; otherwise raises `RerankerOutputInvalidError`.
   - Ranks candidates in descending order of cross-encoder relevance score (1-based rank).
2. **Mock Adapter (`backend/app/adapters/reranking/mock.py`)**:
   - Implements `MockReranker` for test isolation and the `mock` profile at $0 cost without external model files.
   - Supports deterministic query term overlap scoring and explicit `score_map` injection.
3. **Graph Node 4 Integration (`backend/app/core/graph.py`)**:
   - Node 4 (`node_rerank`) maps `state["evidence_ids"]` to `RerankCandidate` using `runtime.context.evidence_text`.
   - Evaluates reranking when `ports.reranker` is present and `ports.mock_reranker_down` is false.
   - If an exception occurs, logs a warning and returns unranked `state["evidence_ids"]`.
4. **Wiring (`backend/app/wiring.py`)**:
   - `create_reranker(profile)` wires `MockReranker` on `mock` and `BgeRerankerAdapter` on real profiles (`desktop-gpu`, `home-gpu`, `office-cpu`, `dev-free-api`, `cloud`).
   - `create_production_ports` and `create_mock_ports` inject `reranker`.

## Verification & Compliance
- `backend/tests/unit/test_reranker_node.py` (19 passed):
  - Proves Node 4 reorders evidence IDs based on descending score.
  - Proves mock reranker down preserves original candidate order.
  - Proves reranker exceptions fall back cleanly to unranked candidates.
  - Proves empty evidence IDs return empty lists immediately.
  - Proves end-to-end participation in compiled graph execution.
  - Proves adapter configuration, output validation, and error handling.
- `backend/tests/unit/test_wiring_profile_guards.py` (41 passed):
  - Proves `create_reranker` profile allowlists and guards.

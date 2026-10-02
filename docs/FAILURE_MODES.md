# Failure Modes and Fallback Matrix

Source of truth: `Client Knowledge Assistant Architecture and Build Plan.md` (Sections: "Query flow" and "Failure handling and fallbacks").

In accordance with Invariant 9, component failure may degrade retrieval quality, but every knowledge answer must satisfy the evidence gate (Node 5 `assess_evidence`) or fail closed.

### The 9-Node Query Flow
1. `guard_input`: Length limits, injection checks, rate limit
2. `route`: Classifies query path and picks answer mode
3. `retrieve`: Hybrid search (vector + keyword), tenant-filtered, RRF merge
4. `rerank`: Cross-encoder candidate reranker
5. `assess_evidence`: Gate setting `evidence_state`
6. `vision_read`: Question-time multimodal page reading
7. `generate`: Structured answer synthesis
8. `validate`: Quote matching, normalized visual comparison, table row check
9. `respond`: Streams answer, status lines, sources, thumbnails
- `safe_failure`: Unrecoverable errors after retries with public error reference

---

| Failure | Affected Node / Component | Fallback Mechanism | What the User Sees | Automated Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **Model timeout** | Node 7 (`generate`), Node 6 (`vision_read`), Node 2 (`route`) | Node retry with backoff (LangGraph retry policy) | Nothing, if the retry succeeds | `tests/integration/test_failure_modes.py::test_model_timeout_retry_success` |
| **Model down after retries** | Node 7 (`generate`), Node 6 (`vision_read`) | Transitions to `safe_failure` node | "I'm having trouble right now. Reference: ERR-7K42" | `tests/integration/test_failure_modes.py::test_model_down_safe_failure` |
| **Vector search fails** | Node 3 (`retrieve`) | Keyword search only, then rerank, then the evidence gate | An answer only if evidence is still strong, otherwise a refusal | `tests/integration/test_failure_modes.py::test_vector_search_failure_keyword_fallback` |
| **Keyword search fails** | Node 3 (`retrieve`) | Vector search only, then the evidence gate | Same rule | `tests/integration/test_failure_modes.py::test_keyword_search_failure_vector_fallback` |
| **Reranker fails** | Node 4 (`rerank`) | Merged results unranked, then the evidence gate | Same rule | `tests/integration/test_failure_modes.py::test_reranker_failure_raw_candidates` |
| **Vision read fails on a page** | Node 6 (`vision_read`) | Use OCR text if its confidence passes; otherwise exclude the page | "Page 31 couldn't be read clearly. Open the source to check." | `tests/integration/test_failure_modes.py::test_vision_read_failure_ocr_fallback` |
| **Validation fails** | Node 8 (`validate`) | Regenerate once (back to Node 7), then refuse (Node 9) | "I couldn't confirm that from the documents." | `tests/integration/test_failure_modes.py::test_validation_failure_retry_and_refuse` |
| **Evidence conflicting** | Node 5 (`assess_evidence`) | Show both sources, no single answer (route to Node 9) | "Two documents disagree:" with both citations | `tests/integration/test_failure_modes.py::test_conflicting_evidence_presentation` |
| **Evidence weak or missing** | Node 5 (`assess_evidence`) | Refuse (route to Node 9) | "I don't have that in Client A's documents." | `tests/integration/test_failure_modes.py::test_weak_evidence_refusal` |
| **Ingestion step fails on a page** | Background Ingestion Pipeline | Retry that page and step only; then mark for review | "Ready (1 page needs review)" on Knowledge page | `tests/integration/test_pipeline_idempotency.py::test_ingestion_page_failure_flagged` |
| **Database unavailable** | Node 3 (`retrieve`), `app.db` | Fail closed, no answer from model memory | Error with reference | `tests/integration/test_failure_modes.py::test_database_down_fail_closed` |
| **Process crash mid-question** | LangGraph Orchestrator | Resume from the last checkpoint by `thread_id` | The answer arrives, slightly late | `tests/integration/test_failure_modes.py::test_process_crash_resumption_from_checkpoint` |
| **Tracer / telemetry store failure** | `Tracer` / `run_steps` | Engine catches error and emits operational log warning; request completes normally (fail-safe contract) | Normal answer uninterrupted; no user-visible error | `tests/integration/test_db_rls_and_telemetry.py::test_tracer_failure_does_not_abort_request` |

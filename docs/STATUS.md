# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile (Active execution on 8 GB office PC with mock profile)

## Current Task
- **Task 1.2**: Architecture import boundary enforcement rules (AST import analyzer verifying `backend/app/core/`, `app/ports/`, `app/adapters/`, and composition root boundaries) — **COMPLETED**

## Next Task
- **Task 1.3**: Port interface definitions in `backend/app/ports/` (pure ABC interfaces for `ModelProvider`, `EmbeddingProvider`, `Reranker`, `DocumentParser`, `StorageBackend`, `JobQueue`, `AuthProvider`, `Tracer`, and `Retriever`)

## Blockers
- **None for Phase 1**: Phase 1 operates entirely on the `mock` profile with synthetic data on the 8 GB office PC (Docker memory capped at 3 GB).
- **External Governance Gates (Phase 6/7 Prerequisites)**:
  - Pharmacy system Gemini key migration to Vertex AI under GCP BAA.
  - Formal retention periods agreement with Dan and compliance.
  - Pilot client practice BAA execution before live data ingestion.

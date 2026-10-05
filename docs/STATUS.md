# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile — **COMPLETED** (Exit gate verified in `docs/phases/PHASE-1-GATE.md`)
- **Phase 2**: Ingestion Pipeline (Runs on office PC then desktop-gpu; exit gate: Golden docs ingested twice with zero duplicates)

## Current Task
- **Task 1.9**: Phase 1 Close-out, Gate Verification Report, and GitHub Actions CI — **COMPLETED**

## Next Task
- **Task 2.1**: Ingestion Pipeline Scaffolding, Docling Parser Port, and Ingestion Graph Architecture




## Blockers
- **None for Phase 1**: Phase 1 operates entirely on the `mock` profile with synthetic data on the 8 GB office PC (Docker memory capped at 3 GB).
- **External Governance Gates (Phase 6/7 Prerequisites)**:
  - Pharmacy system Gemini key migration to Vertex AI under GCP BAA.
  - Formal retention periods agreement with Dan and compliance.
  - Pilot client practice BAA execution before live data ingestion.

# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile — **COMPLETED** (Exit gate verified in `docs/phases/PHASE-1-GATE.md`)
- **Phase 2**: Ingestion Pipeline (Runs on office PC then desktop-gpu; exit gate: Golden docs ingested twice with zero duplicates)

## Current Task
- **Task 2.4**: Docling Parser Adapter & Tri-Path Extraction — **IN PROGRESS**
  - Completed: Review policy with MECE coverage for all 4 page types, Migration 0005 applied to shared database (`0005_readings_table_cites`), database seeding test fixtures decoupled to `tests/fixtures/database_seeding.py`, parser profile matrix routing (`mock` -> `MockDocumentParser`, `office-cpu`/`desktop-gpu`/`home-gpu`/`dev-free-api` -> `DoclingDocumentParser`, `cloud` -> `DoclingDocumentParser` with offline/pinned models enforced), and adapter offline configuration (`EasyOCR` download disabled, model directory set, `HF_HUB_OFFLINE=1`).
  - Pending: Manual execution of real-model desktop run (`desktop-gpu`) on the 32 GB workstation.

## Next Task
- **Task 2.5**: Document Chunker (Text Chunking, Metadata, Context Prefixes & Invariant 6 Enforcement)

## Pending Implementation Notes
- **Upload API (Phase 2 upload API task)**: The upload endpoint must open `tenant_session(ctx)` itself and pass that session to `create_upload_service`. Add a test in that task proving uploads run with `app.current_tenant_id` set.
- **Version Activation (Phase 2 activation task)**: Version activation must flip `documents.active_version_id` and `chunks.is_active` in one transaction, with a test, in the task that builds activation.
- **Storage Adapter (Phase 2 storage task)**: `StorageKey` must take `tenant_id` from `TenantContext` as a UUID and reject `..`, absolute paths, and backslashes in Python as well.
- **Parser Wiring Profile Guard**: `MockDocumentParser` is strictly wired only in profile `mock`. `office-cpu`, `desktop-gpu`, `home-gpu`, and `dev-free-api` wire `DoclingDocumentParser`. `cloud` profile strictly enforces `disable_model_download=True` and `pinned_model_paths` and forwards them to `DoclingDocumentParser`. All unknown profiles raise `ConfigurationError`.
## Blockers
- **None for Phase 1**: Phase 1 operates entirely on the `mock` profile with synthetic data on the 8 GB office PC (Docker memory capped at 3 GB).
- **External Governance Gates (Phase 6/7 Prerequisites)**:
  - Pharmacy system Gemini key migration to Vertex AI under GCP BAA.
  - Formal retention periods agreement with Dan and compliance.
  - Pilot client practice BAA execution before live data ingestion.

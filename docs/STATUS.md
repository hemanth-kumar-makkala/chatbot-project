# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile — **COMPLETED** (Exit gate verified in `docs/phases/PHASE-1-GATE.md`)
- **Phase 2**: Ingestion Pipeline (Runs on office PC then desktop-gpu; exit gate: Golden docs ingested twice with zero duplicates)

## Current Task
- **Task 2.3**: Document Splitting & Page Classification — **COMPLETED**

## Next Task
- **Task 2.4**: Docling Parser Adapter & Tri-Path Extraction
  - Scope: Implement DocumentParser adapter wrapping Docling in adapters/parsing/docling.py to handle digital text/tables and scanned OCR extraction paths. Tests to write first in tests/integration/test_docling_parser.py.

## Pending Implementation Notes
- **Upload API (Phase 2 upload API task)**: The upload endpoint must open `tenant_session(ctx)` itself and pass that session to `create_upload_service`. Add a test in that task proving uploads run with `app.current_tenant_id` set.
- **Version Activation (Phase 2 activation task)**: Version activation must flip `documents.active_version_id` and `chunks.is_active` in one transaction, with a test, in the task that builds activation.
- **Storage Adapter (Phase 2 storage task)**: `StorageKey` must take `tenant_id` from `TenantContext` as a UUID and reject `..`, absolute paths, and backslashes in Python as well.
## Blockers
- **None for Phase 1**: Phase 1 operates entirely on the `mock` profile with synthetic data on the 8 GB office PC (Docker memory capped at 3 GB).
- **External Governance Gates (Phase 6/7 Prerequisites)**:
  - Pharmacy system Gemini key migration to Vertex AI under GCP BAA.
  - Formal retention periods agreement with Dan and compliance.
  - Pilot client practice BAA execution before live data ingestion.

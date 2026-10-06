# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile — **COMPLETED** (Exit gate verified in `docs/phases/PHASE-1-GATE.md`)
- **Phase 2**: Ingestion Pipeline (Runs on office PC then desktop-gpu; exit gate: Golden docs ingested twice with zero duplicates)

## Current Task
- **Task 2.1**: Ingestion Database Schema & Idempotency Migration — **COMPLETED**

## Next Task
- **Task 2.2**: Document Upload Validation & Tenant-Scoped Deduplication
  - Scope: Build upload handler verifying file headers (magic bytes), size limits, malware check stub, and tenant-scoped hash matching (Invariant 15). Catch duplicate `(tenant_id, sha256)` uploads and return existing version instead of raising `IntegrityError`.

## Pending Implementation Notes
- **Upload Service (Task 2.2)**: For `(tenant_id, sha256)` duplicate uploads, the upload service must catch the duplicate / query existing record and return the existing version instead of raising `IntegrityError`.
- **Version Activation (Phase 2 activation task)**: Version activation must flip `documents.active_version_id` and `chunks.is_active` in one transaction, with a test, in the task that builds activation.
- **Storage Adapter (Phase 2 storage task)**: `StorageKey` must take `tenant_id` from `TenantContext` as a UUID and reject `..`, absolute paths, and backslashes in Python as well.
## Blockers
- **None for Phase 1**: Phase 1 operates entirely on the `mock` profile with synthetic data on the 8 GB office PC (Docker memory capped at 3 GB).
- **External Governance Gates (Phase 6/7 Prerequisites)**:
  - Pharmacy system Gemini key migration to Vertex AI under GCP BAA.
  - Formal retention periods agreement with Dan and compliance.
  - Pilot client practice BAA execution before live data ingestion.

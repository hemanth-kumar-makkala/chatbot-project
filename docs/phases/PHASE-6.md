# Phase 6: Cloud Staging on GCP

## Goal
Deploy the application to Google Cloud Platform (GCP) staging infrastructure using only BAA-covered services, execute the identical test and evaluation suite on the `cloud` profile using synthetic data only, verify that evaluation scores hold, and measure monthly operating costs against the pilot budget target (<$100/mo).

- **Runs on**: `GCP`.
- **Exit gate**: Scores hold on cloud, monthly cost measured.
- **Data Policy**: Synthetic data only (Invariant 14). No real client data.

## Scope
- Provision GCP infrastructure on BAA-covered services:
  - Cloud Run (API container, worker container, Next.js web container).
  - Cloud SQL for PostgreSQL + pgvector (with Alembic migrations and tenant RLS).
  - Google Cloud Storage (originals, page images, signed short-lived URLs).
  - Secret Manager (replacing local `.env`).
  - Cloud Logging (operational logs, no PHI).
- Vertex AI integration: swappable `ModelProvider` role mappings using Vertex AI (BAA covered; never AI Studio keys).
- Self-hosted BGE-M3 embedding service in GCP to avoid re-embedding documents or changing vector dimensions (1024-dim).
- Runtime profile activation: `cloud.yaml`.
- Execution of the full synthetic evaluation suite on GCP staging.
- Measurement of cost per question and cost per page ingested to validate the pilot workload budget.

## Out of Scope
- Real client documents or real PHI (Phase 7).
- Public DNS domain cutover.
- Frontier models not covered by Google's BAA.

## Dependencies
- Phase 5 Hardening exit gate passed.
- Formal Google Cloud BAA confirmed in writing with Dan and legal.

## Numbered Tasks

### Task 6.1: GCP Infrastructure Provisioning & BAA Compliance Check
- **Description**: Deploy Cloud Run, Cloud SQL pgvector, Cloud Storage, and Secret Manager using infrastructure-as-code scripts, verifying all services against Google's HIPAA covered list.
- **Tests to write first**:
  - `tests/unit/test_cloud_compliance_manifest.py`: Verify infrastructure definitions utilize only approved BAA-covered service resources.

### Task 6.2: Vertex AI Model Adapter Implementation
- **Description**: Implement `backend/app/adapters/models/vertex.py` wrapping Vertex AI Generative AI SDK with structured JSON response schemas and system instructions.
- **Tests to write first**:
  - `tests/integration/test_vertex_adapter.py`: Test Vertex AI connectivity and verify JSON schema enforcement on staging credentials.

### Task 6.3: Google Cloud Storage (GCS) Adapter
- **Description**: Implement `StorageBackend` interface in `backend/app/adapters/storage/gcs.py` with signed short-lived URL generation for PDF and thumbnail access.
- **Tests to write first**:
  - `tests/integration/test_gcs_storage_adapter.py`: Test file upload, download, and short-lived signed URL generation.

### Task 6.4: Cloud SQL Migration and Tenant RLS Verification
- **Description**: Run Alembic migrations against staging Cloud SQL instance, verify pgvector extension, and assert RLS policies isolate tenant queries.
- **Tests to write first**:
  - `tests/integration/test_cloud_sql_rls.py`: Assert cross-tenant queries fail with RLS active on Cloud SQL.

### Task 6.5: Self-Hosted BGE-M3 Service Topology on GCP
- **Description**: Deploy containerized BGE-M3 embedding service on GCP (evaluating separate internal Cloud Run service vs co-location inside API/worker container based on cold-start latency and cost measurements) to serve embedding requests identically to the local environment without re-indexing.
- **Tests to write first**:
  - `tests/integration/test_cloud_bge_m3.py`: Assert embedding outputs match 1024-dim vectors generated locally.

### Task 6.6: Cloud Staging Evaluation Run & Metric Verification
- **Description**: Execute the 100+ golden set questions in GCP staging on the `cloud` profile; record metrics in `evals/reports/staging_baseline.json` and verify scores hold or improve compared to `desktop-gpu`.
- **Tests to write first**:
  - `tests/eval/test_staging_regression.py`: Assert staging scores match or exceed local baselines.

### Task 6.7: Operating Cost Measurement & Daily Spend Caps
- **Description**: Measure token usage, storage costs, and compute utilization during evaluation runs; configure tenant daily spend caps and GCP budget alerts.
- **Tests to write first**:
  - `tests/unit/test_cloud_spend_caps.py`: Assert spend tracking records costs and triggers alerts when quota thresholds are approached.

## Exit Gate (from Spec)
- [ ] Scores hold on cloud compared to local baseline.
- [ ] Monthly cost measured and verified against pilot budget (<$100/mo).

## Risks
- Vertex AI quota throttling during large batch evaluation runs.

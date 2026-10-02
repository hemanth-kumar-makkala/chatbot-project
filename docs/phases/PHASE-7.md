# Phase 7: Pilot with One Client

## Goal
Conduct a two-week live pilot with the first client using real data under an executed practice BAA on GCP, operating side-by-side with Chatbase, followed by rollout to remaining clients and complete Chatbase decommissioning upon Dan's formal sign-off.

- **Runs on**: `GCP`.
- **Exit gate**: Dan signs off and Chatbase is cancelled.
- **Prerequisite**: Phase 5 Hardening (security, cross-tenant, injection, load tests, backups) MUST be fully completed and passed before Phase 7 begins.

## Scope
- Formal BAA execution between Staffingly and the pilot client medical practice.
- Pilot client tenant provisioning (`tenants`, `assistants`, `memberships`).
- Live client document upload via the Knowledge page; client admin review of pages flagged as `Needs review`.
- Daily staff usage:
  - Querying knowledge documents for prior auth, clinical processes, and payer rules.
  - Chat session uploads of patient faxes for structured field extraction.
- Side-by-side comparison with Chatbase over a 2-week active operational window.
- Staff feedback monitoring (`feedback` table) and operational telemetry audit.
- Full cutover and decommissioning of Chatbase widget for the pilot client.
- Onboarding plan for remaining practice clients.

## Out of Scope
- Security, cross-tenant, or load testing (these are strictly completed in Phase 5).
- Patient-facing chat or autonomous EHR write-backs.
- Generic admin dashboards (only the Knowledge page exists).

## Dependencies
- Phase 6 Cloud Staging exit gate passed.
- Fully executed practice BAA with the pilot client.

## Numbered Tasks

### Task 7.1: Client Tenant Provisioning & BAA Verification
- **Description**: Validate mutual practice BAA execution, provision tenant records, assign user memberships (`client_admin` and `staff`), and set tenant spend quotas.
- **Tests to write first**:
  - `tests/integration/test_client_tenant_provisioning.py`: Assert newly provisioned tenant exhibits isolated storage paths and zero access to existing test data.

### Task 7.2: Live Document Ingestion & Knowledge Page Review
- **Description**: Ingest pilot client documents; verify that low-confidence OCR pages are routed to the Knowledge page review list for client admin inspection.
- **Tests to write first**:
  - `tests/integration/test_admin_review_flow.py`: Verify that documents with flagged pages transition to `Ready (N pages need review)` and allow client admin verification.

### Task 7.3: Two-Week Side-by-Side Operational Pilot
- **Description**: Enable staff access to Client A Assistant alongside Chatbase for 14 days, monitoring query latency, citation accuracy, and refusal behavior.
- **Tests to write first**:
  - `tests/integration/test_pilot_telemetry.py`: Verify live telemetry records metadata in operational logs with zero PHI.

### Task 7.4: Staff Feedback Review and Quality Audit
- **Description**: Review collected feedback from the `feedback` table, evaluate unreadable/conflicting field extraction flags, and verify answer faithfulness.
- **Tests to write first**:
  - `tests/integration/test_feedback_pipeline.py`: Assert submitted ratings feed cleanly into quality reporting.

### Task 7.5: Chatbase Cancellation & Full Cutover
- **Description**: Secure Dan's formal sign-off based on side-by-side pilot performance, decommission the Chatbase widget, and initiate onboarding for remaining clients.
- **Tests to write first**:
  - N/A (Executive sign-off and operational decommissioning).

## Exit Gate (from Spec)
- [ ] Dan signs off and Chatbase is cancelled.

## Risks
- Unexpected spikes in client upload volumes exceeding initial Cloud SQL storage allocations.

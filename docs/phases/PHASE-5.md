# Phase 5: Hardening

## Goal
Harden the system locally before any cloud staging or real data exposure by implementing guardrails (Node 1 `guard_input`), adversarial prompt injection suites, cross-tenant isolation suites, rate limits, Presidio exception scrubbing, user feedback capture, Locust load capacity curve profiling, and PostgreSQL backup/restore verification.

- **Runs on**: `desktop-gpu`.
- **Exit gate**: Security suite passes, capacity curve drawn.
- **Fixed Ordering Rule**: Security, cross-tenant, and load testing MUST occur in Phase 5 before any cloud deployment (Phase 6) or real data pilot (Phase 7). They never move to the pilot.

## Scope
- Input Guardrails (Node 1 `guard_input`): length validation, prompt injection pattern detection, language detection, per-user and per-tenant rate limits.
- Output Guardrails: cross-tenant leak checks, citation existence checks, token ceiling enforcement.
- General task route guardrails: allow drafting/explaining, but strictly block stating client facts or clinical/payer rules without documents.
- Adversarial Security Test Suite:
  - `INJECTION` test suite: instructions hidden inside uploaded PDFs and user prompts.
  - `CROSS_TENANT` adversarial test suite: identical file names, patient names, and queries across isolated tenants; leak count MUST be zero (Invariant 15).
- Log Redaction Safety Net: Microsoft Presidio integration scrubbing stack traces and exception messages before writing operational logs.
- User feedback mechanism: thumbs up/down and reason capture writing to the `feedback` table.
- Capacity & Load Testing via Locust: stepped concurrency (5, 10, 20, 50, 100 users) generating p50/p95 latency and throughput capacity curve.
- Disaster recovery: automated PostgreSQL pgvector backup script with a tested, automated restore verification.

## Out of Scope
- Cloud deployment on GCP (Phase 6).
- Handling un-redacted real patient data (Phase 7).
- Generic admin dashboards.

## Dependencies
- Phase 3 retrieval/chat UI and Phase 4 vision/extraction modules.
- Profile `desktop-gpu` active for local load and security verification.

## Numbered Tasks

### Task 5.1: Input Guardrails & Injection Filtering (Node 1 `guard_input`)
- **Description**: Implement Node 1 validating query length, rate limits per user/tenant, prompt-injection patterns, routing directly to Node 9 with refusal if violated.
- **Tests to write first**:
  - `tests/unit/test_input_guards.py`: Assert malicious prompt injections and oversized payloads are blocked at Node 1.

### Task 5.2: Adversarial Injection Test Suite
- **Description**: Build automated adversarial injection test suite executing synthetic documents containing hidden override instructions against retrieval and generation flows.
- **Tests to write first**:
  - `tests/integration/test_adversarial_injection.py`: Assert injection success rate is exactly zero (system adheres to evidence gate).

### Task 5.3: Adversarial Cross-Tenant Isolation Test Suite
- **Description**: Implement comprehensive cross-tenant adversarial test suite executing simultaneous queries across mock tenants with identical document titles and patient records.
- **Tests to write first**:
  - `tests/invariants/test_adversarial_cross_tenant_isolation.py`: Assert cross-tenant leak count is strictly zero (Invariants 2, 3, 15).

### Task 5.4: Presidio Exception Telemetry Scrubber — COMPLETED & VERIFIED
- **Description**: Integrate Microsoft Presidio analyzer in the error handling layer to scrub exception traces before emitting operational log lines.
- **Tests to write first**:
  - `tests/unit/test_presidio_scrubber.py`: Verify synthetic exception payloads containing PHI patterns are scrubbed before logging.

### Task 5.5: Feedback Collection Mechanism
- **Description**: Implement API route and UI buttons for message rating (thumbs up/down) with optional text reason, persisting to the `feedback` table.
- **Tests to write first**:
  - `tests/integration/test_feedback_api.py`: Verify feedback submissions persist to database and associate correctly with `message_id`.

### Task 5.6: Capacity & Load Curve Profiling via Locust — COMPLETED & VERIFIED
- **Description**: Configure Locust load test scripts simulating 5, 10, 20, 50, and 100 concurrent users to measure p50 and p95 latencies for text and vision paths.
- **Tests to write first**:
  - `tests/eval/test_locust_capacity_curve.py`: Execute load test runner, assert error rate remains below threshold, and generate capacity curve report in `evals/reports/`.

### Task 5.7: Database Backup & Tested Restore Automation
- **Description**: Implement automated database backup script for PostgreSQL pgvector data and author an automated test that wipes and restores from backup, verifying vector index and tenant integrity.
- **Tests to write first**:
  - `tests/integration/test_backup_restore.py`: Execute backup, purge database, restore from backup, and verify document and vector counts match exactly.

## Exit Gate (from Spec)
- [x] Security suite passes (zero cross-tenant leaks, zero injection bypasses).
- [x] Capacity curve drawn across 5, 10, 20, 50, and 100 virtual users.
- [x] Presidio exception scrubbing active.
- [ ] Backup creation and automated restore verified.

## Risks
- CPU throttling during 100-user local load testing on the desktop workstation.

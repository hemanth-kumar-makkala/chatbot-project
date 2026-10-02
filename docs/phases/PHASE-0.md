# Phase 0: Foundations

## Goal
Establish foundational legal/compliance guardrails, agree upon operational retention and workload parameters, generate synthetic multi-modal evaluation datasets, and create initial interface skeletons without touching real patient or client data.

- **Runs on**: Planning, no machine needed.
- **Parallel Execution**: Phase 1 may run in parallel with Phase 0 on the mock profile.

## Scope
- Compliance review of existing AI Studio key usage in the pharmacy system and migration plan to Vertex AI under GCP BAA.
- Agreement on data retention periods for messages, chat-uploaded session files, graph checkpoints, and execution traces with Dan and compliance.
- Definition of the pilot workload metrics (concurrent users, questions/day, ingested pages/month).
- Selection of the initial pilot client and acquisition of an approved de-identified sample.
- Creation of 20 to 30 synthetic multi-modal documents spanning all test categories (`TEXT_FACT`, `VISUAL_RECOVERY`, `TABLE_LOOKUP`, `NO_ANSWER`, `CONFLICT`, `MULTILINGUAL`, `LENGTH`, `EXTRACTION`, `CROSS_TENANT`, `INJECTION`).
- Authoring 100+ labelled synthetic question-answer-evidence benchmark pairs.
- Repository skeleton setup, dependency version pinning, and UI design specification drafting with Staffingly's brand palette.

## Out of Scope
- Backend application logic implementation.
- Cloud resource provisioning or deployment.
- Handling or ingesting real protected health information (PHI).

## Dependencies
- Architecture Spec (`Client Knowledge Assistant Architecture and Build Plan.md`).
- Compliance consultation (Dan / Legal).
- Pilot client agreement in principle.

## Numbered Tasks

### Task 0.1: Governance and Compliance Baseline
- **Description**: Document formal compliance requirements, review the existing pharmacy system's Gemini AI Studio key, plan its Vertex AI migration, and formalize data retention rules.
- **Tests to write first**:
  - `tests/unit/test_retention_policy_config.py`: Unit test verifying default retention duration schemas parse correctly and reject infinite retention values for session uploads.

### Task 0.2: Pilot Workload and Capacity Targets Definition
- **Description**: Formalize target performance benchmarks: N concurrent users, target p95 latency curve, and monthly ingestion volume targets for local and cloud pilot phases.
- **Tests to write first**:
  - `tests/eval/test_workload_benchmarks.py`: Assert that defined workload parameters satisfy budget constraints ($0 local, <$100/mo cloud).

### Task 0.3: Synthetic Document Golden Dataset Construction
- **Description**: Author 20-30 synthetic documents representing native text PDFs, scanned faxes with degraded quality, complex tables, multilingual text, and adversarial prompt injections in `evals/golden/`.
- **Tests to write first**:
  - `tests/eval/test_dataset_schema.py`: Validate that every synthetic document fixture adheres to the golden manifest schema and has no real PHI identifiers.

### Task 0.4: Labelled Evaluation Benchmark Questions Creation
- **Description**: Author 100+ ground-truth labelled questions across the 10 test categories mapped directly to synthetic document pages and chunk IDs in `evals/golden/`.
- **Tests to write first**:
  - `tests/eval/test_golden_questions.py`: Verify that all 100+ questions reference existing synthetic fixture IDs and specify expected answer mode and evidence type.

### Task 0.5: Repository Skeleton and Version Pinning
- **Description**: Bootstrap repository file tree, `.env.example`, `.gitignore`, configuration profiles, and pin all Python package versions in requirements.
- **Tests to write first**:
  - `tests/unit/test_environment_spec.py`: Verify requirements lock file has pinned versions without open ranges (e.g. `==` only).

### Task 0.6: UI Design Specification Drafting
- **Description**: Produce the UI design specification incorporating Staffingly's brand palette, citation source chips, side-panel PDF page viewer, and extraction view.
- **Tests to write first**:
  - N/A (Design artifact review).

## Exit Gate (from Spec)
- [ ] Compliance route confirmed in writing.
- [ ] Retention periods and pilot workload agreed in writing.
- [ ] First pilot client selected; de-identified sample confirmed.
- [ ] 20-30 synthetic multi-modal documents built in `evals/golden/`.
- [ ] 100+ labelled questions created covering all test categories.
- [ ] Repository skeleton created with pinned dependencies.
- [ ] UI design spec completed with brand palette.

## Risks
- Delay in compliance sign-off for Vertex AI migration.
- Synthetic documents failing to reflect real-world fax artifacts or tabular layouts.

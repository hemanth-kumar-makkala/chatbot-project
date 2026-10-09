# ADR-0010: Document Version Activation and Review Policy

- **Status**: Proposed
- **Date**: 2026-10-09
- **Author**: Srinath

## Context & Problem Statement
The Client Knowledge Assistant ingests documents asynchronously via background worker jobs running per-page pipelines (Task 2.7). As pages are processed, visual readings, tables, and text chunks are persisted. When all pages of a document version finish processing, the version must transition from `processing` to an active or terminal state.

During ingestion, individual pages may be flagged as requiring client admin review (`pages.needs_review = True`), for instance due to low OCR confidence scores or visual reading warnings.

The system must define an unambiguous, mutually exclusive and collectively exhaustive (MECE) activation policy addressing:
1. How to handle new versions with flagged pages when an existing active version is currently serving queries.
2. How to handle new versions with flagged pages when no prior version exists (initial upload).
3. How to cleanly retire older versions when an unflagged version finishes.
4. How to serialize concurrent page completions without race conditions or deadlocks.

## Decision Drivers
- **Invariant 4**: *"Every knowledge answer has sufficient evidence or refuses."*
- **Invariant 8**: *"Every answer traces to a document version and page."*
- **Invariant 11**: *"All ingestion steps are resumable and idempotent."*
- **Service Continuity (Zero Downtime)**: Uploading an updated document that contains unreadable or blurry pages must not break or take down existing Q&A capabilities supported by a previously verified version.
- **Immediate Availability of Legible Evidence**: For brand-new documents containing some flagged pages, the readable portions should become searchable immediately rather than blocking the entire document indefinitely.
- **Concurrency & Deadlock Prevention**: Procrastinate page jobs execute concurrently; multiple final pages completing simultaneously must not create split activations, lost updates, or deadlock database worker sessions.

## Deviation from Spec
The architecture specification (`Client Knowledge Assistant Architecture and Build Plan.md`, lines 251 and 261) states:
> *"End states: Ready (searchable, citations by page) or Needs review (low-confidence pages shown to the client admin; the rest of the file stays usable)."*
> *"A new version of a file becomes active only when all its pages are processed or marked for review; the old version's chunks are then retired, not deleted, for audit."*

Under a strict reading of the spec rule, every newly processed version would become active immediately upon completion, activating clean pages, holding flagged pages out of index, and retiring the prior version. Branch A deliberately deviates from this spec baseline: when a prior active version exists and the new version has flagged pages, Branch A preserves the prior version as active and holds the incoming version in `needs_review` with all its chunks inactive until approved by the client admin.

## Considered Options
1. **Option 1: Spec Baseline Activation**: Activate the new version (`documents.active_version_id = new_version_id`), hold flagged pages out of index (`is_active = False`), activate clean pages (`is_active = True`), and retire the previous version (`status = 'retired'`, previous chunks `is_active = False`).
   - *Risk*: If the new version contains unreadable tables or dropped clauses on flagged pages, retiring the older version immediately may degrade answer quality for questions that the older version answered reliably.
2. **Option 2: Strict Blocking (All-or-Nothing)**: Do not activate any version that has flagged pages. Keep the document completely inactive if it is the first version.
   - *Flaw*: A 100-page policy manual with 1 low-confidence page would remain completely unavailable to the client admin until manual review, even though 99 pages are pristine and legible.
3. **Option 3: MECE 3-Branch Activation Policy with Strict Global Lock Order (Chosen)**.

## Decision Outcome
Adopt **Option 3**. The activation logic is implemented in `app/core/ingestion/activation.py` (`VersionActivationService`) and executed atomically in `app/adapters/db/postgres_document_repository.py`.

### 1. MECE Activation Branches

- **Branch A (Previous active version exists AND new version has flagged pages)**:
  - **Policy**: Keep the previous active version active (`documents.active_version_id` remains unchanged).
  - **New Version Status**: Set to `needs_review`.
  - **Chunks**: All chunks of the new version remain inactive (`is_active = False`).
  - **Rationale**: Preserves continuous service without degrading answer quality. Retrieval continues using the verified prior version while the client admin reviews the flagged pages of the incoming version in the Knowledge UI.
  - **Open Decision (Stale-Answer Risk)**: If the new version was uploaded to replace outdated rates, policies, or dosages, keeping the old version active causes the assistant to continue answering from stale information until the client admin completes review. Whether Branch A should retain the old version or adopt Option 1 (retire old version, activate clean pages) remains an open architectural decision for Srinath.

- **Branch B (First version of document AND new version has flagged pages)**:
  - **Policy**: Activate the version (`documents.active_version_id = new_version_id`).
  - **Version Status**: Set to `needs_review`.
  - **Chunks**: Chunks belonging to clean pages (`needs_review = False`) are marked active (`is_active = True`). Chunks belonging to flagged pages (`needs_review = True`) remain inactive (`is_active = False`, held out of index).
  - **Rationale**: Maximizes immediate utility for newly uploaded documents without risking evidence integrity. Flagged pages do not pollute search results until reviewed and cleared.

- **Branch C (Zero flagged pages / all pages clean)**:
  - **Policy**: Activate the new version (`documents.active_version_id = new_version_id`).
  - **Version Status**: Set to `ready`.
  - **Chunks**: All chunks of the new version are marked active (`is_active = True`).
  - **Retirement**: If a previous active version exists, it is atomically retired (`status = 'retired'`, previous chunks set to `is_active = False`).

### 2. Strict Global Lock Hierarchy & Concurrency Control
To prevent race conditions where concurrent page completions double-activate or miss the final completion check, and to eliminate database deadlocks:
1. **Transaction Isolation**: Page work (inserting chunks, updating `job_steps`) commits in its own short-lived page transaction.
2. **Activation Lock Order**: The completion check and activation run in a dedicated transaction enforcing a strict top-down lock order:
   - Step 1: Lock the parent `documents` row (`SELECT ... FOR UPDATE`).
   - Step 2: Lock the child `document_versions` row (`SELECT ... FOR UPDATE`).
   - Step 3: Lock the child `pages` rows (`SELECT ... FOR UPDATE`).
3. **Terminal Gate**: If `document_versions.status != 'processing'`, the transaction exits immediately as a no-op.
4. **Transient Error Retry**: Database deadlocks (`40P01`) and serialization failures (`40001`) are classified as transient retryable errors handled by Procrastinate retry policies (pending implementation of Item 2).

## Consequences & Trade-offs
- **Positive**:
  - Existing verified knowledge is never replaced by unverified content without human review.
  - Zero downtime during document updates.
  - Clean pages in initial uploads are immediately usable.
  - Atomic switching of `active_version_id` and chunk `is_active` flags ensures zero window of inconsistent state.
  - Strict lock order guarantees deadlock-free concurrent page completion.
- **Negative & Identified Risks**:
  - **Client Admin Monitoring**: The client admin must actively monitor the Knowledge UI review list to approve or re-run flagged pages.
  - **Stale Answers**: In Branch A, answers reflect the previous document version until review is completed.
  - **Partial Knowledge**: In Branch B, queries on brand-new documents will miss information from flagged pages until the client admin resolves them.
  - **Crash Between Transactions**: If a worker crashes after committing Transaction 1 on the final page but before Transaction 2 executes, the document version can become stalled in `processing`. Mitigated by recovery in `enqueue_uncompleted_page_jobs` and periodic stalled job sweep.
  - **All-Pages-Flagged Edge Case**: If 100% of pages in an initial upload fail or are flagged, Branch B activates the version with 0 active chunks. The version enters `needs_review` and queries on the document cleanly refuse under Invariant 4.

## Compliance & Invariant Verification
- **Invariant 4 & 8 Verification**: Automated tests verify that chunks from flagged pages in Branch A and Branch B never receive `is_active = True` prior to manual resolution.
- **Invariant 11 Verification**: Idempotency and crash re-run recovery tests verify that duplicate chunks are never created and stalled versions safely activate.
- **Test Coverage**:
  - `backend/tests/integration/test_version_activation.py`:
    - `test_activation_branch_a_existing_active_version_with_flagged_pages`
    - `test_activation_branch_b_first_version_with_flagged_pages`
    - `test_activation_branch_c_no_flagged_pages`
    - `test_activation_concurrency_race_with_separate_sessions`
  - `backend/tests/integration/test_pipeline_idempotency.py`:
    - `test_all_pages_done_stuck_processing_activated_by_recovery`
- **Architecture Boundaries**: Core activation service (`app/core/ingestion/activation.py`) depends only on abstract port `DocumentRepository` (`app/ports/document_repository.py`), ensuring zero direct database/ORM couplings.

# Phase 4: Vision and Extraction

## Goal
Implement question-time vision reading of scanned/image pages (Node 6 `vision_read`), visual comparison using normalized values in Node 8 `validate`, chat session PHI document upload, multi-reader extraction with dual read comparison, explicit field state assignment, and the side-by-side extraction table UI beside the page image.

- **Runs on**: `desktop-gpu`.
- **Exit gate**: False-fill and visual recovery baselines recorded.

## Scope
- Question-time vision reading (Node 6 `vision_read`): sends page image and question to `qwen3-vl:4b` when Node 5 sets `evidence_state=VISUAL_REQUIRED`. Fallback to OCR text if OCR confidence passes; otherwise refuse for that page.
- Visual validation in Node 8 (`validate`): compare question-time reading with upload-time reading using normalized values (digits, dates, IDs, amounts; prose may differ, but IDs must not). Disagreement flags answer. Always attach page thumbnail (Invariant 5).
- Chat-uploaded PHI document handling: persisted in `session_documents`, strictly excluded from shared knowledge index (Invariant 7).
- Structured extraction pipeline:
  - `extract_fields`: runs both OCR read and vision read.
  - `dual_read_compare`: reconciles two independent readings.
  - `classify_confidence`: determines confidence tiers.
  - `validate_fields`: executes format and check-digit validators (date, ID format, NPI check digit).
  - Explicit field states (Invariant 6): `FILLED_HIGH`, `FILLED_MEDIUM`, `CONFLICTING`, `UNREADABLE`, `INVALID`, `NOT_PRESENT`, `NOT_APPLICABLE`. Unreadable fields are blank and flagged, never guessed.
- Web UI: Field table rendered directly beside the uploaded page image in the chat window, highlighting unreadable and conflicting fields.

## Out of Scope
- Direct EHR or CRM write-backs.
- Patient-facing chat.
- Generic admin dashboards.

## Dependencies
- Phase 3 retrieval engine and chat UI.
- Profile `desktop-gpu` active (`qwen3-vl:4b` on RTX 3050 6GB with image pre-scaling).

## Numbered Tasks

### Task 4.1: Question-Time Vision Reading (Node 6 `vision_read`)
- **Description**: Implement Node 6 taking page image and question, scaling image to fit 6 GB VRAM budget, invoking `ModelProvider.read_page(role="vision")`, with OCR fallback on vision failure.
- **Tests to write first**:
  - `tests/integration/test_vision_read_node.py`: Assert that image pages produce question-time readings and test OCR fallback on model failure.

### Task 4.2: Normalized Visual Value Validation (Node 8 `validate`)
- **Description**: Implement visual validation comparing question-time and upload-time readings using normalized values (digits, dates, IDs, amounts). Attach visual evidence badge and thumbnail citation.
- **Tests to write first**:
  - `tests/unit/test_visual_normalized_validation.py`: Assert matching normalized numbers pass validation even if surrounding prose differs, and assert discrepancies trigger visual warning flag.

### Task 4.3: Session PHI Document Storage & Retention
- **Description**: Implement chat-level document upload handler storing files in `session_documents` with retention timestamps, strictly isolated from `chunks` (Invariant 7).
- **Tests to write first**:
  - `tests/invariants/test_phi_session_isolation.py`: Assert session documents are never returned by knowledge retrieval queries.

### Task 4.4: Dedicated Extraction Subgraph Implementation
- **Description**: Implement the extraction flow as a separate LangGraph subgraph containing `extract_fields` (dual OCR and vision read) and `dual_read_compare` (normalized value reconciliation), invoked when the main graph's Node 2 (`route`) routes to extraction. The subgraph reuses Node 1 (`guard_input`) and Node 9 (`respond`).
- **Tests to write first**:
  - `tests/unit/test_dual_read_compare.py`: Test value comparison across matching and conflicting readings within the extraction subgraph.

### Task 4.5: Field State Classification & Validation Engine
- **Description**: Implement field validator applying formats (dates, IDs, NPI check digits) and assigning explicit states (`FILLED_HIGH`, `FILLED_MEDIUM`, `CONFLICTING`, `UNREADABLE`, `INVALID`, `NOT_PRESENT`, `NOT_APPLICABLE`) (Invariant 6).
- **Tests to write first**:
  - `tests/unit/test_field_state_engine.py`: Assert unreadable fields receive `UNREADABLE` status and are never filled with guesses (Invariant 6).

### Task 4.6: Side-by-Side Extraction UI in Chat Window
- **Description**: Extend `web/` chat interface to render extracted field tables alongside document page previews, highlighting unreadable fields and conflicting readings.
- **Tests to write first**:
  - `web/__tests__/extraction_view.test.tsx`: Test rendering of field table next to document image with proper status chips.

### Task 4.7: False-Fill and Visual Recovery Benchmark Recording
- **Description**: Run evaluation on golden synthetic visual recovery and extraction test sets; record false-fill rate (must be 0 on unreadable fields) and visual recovery Hit@5 in `evals/reports/`.
- **Tests to write first**:
  - `tests/eval/test_phase4_extraction_eval.py`: Assert false-fill rate and visual recovery metrics are recorded to `evals/reports/phase4_baseline.json`.

## Exit Gate (from Spec)
- [ ] False-fill and visual recovery baselines recorded in `evals/reports/`.
- [ ] Visual readings explicitly cited with thumbnails (Invariant 5).
- [ ] Zero unreadable fields guessed or falsely filled (Invariant 6).

## Risks
- Image memory consumption on 6 GB VRAM GPU during dual-read inference (mitigated by pre-scaling page images).

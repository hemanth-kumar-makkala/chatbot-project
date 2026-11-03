# ADR-0019: Ollama qwen3-vl:4b Model Build Update and Re-Measurement Requirements

- **Status**: Accepted
- **Date**: 2026-11-02
- **Author**: Antigravity / Srinath Gollapally

## Context & Problem Statement
On the dedicated GPU desktop deployment environment, `ollama list` reports `qwen3-vl:4b` with model build ID `a1b29c246319` (manifest digest: `sha256:a1b29c246319cdcb1f3842f36d29f198f737c0cf516ba1bcba34b1b974e87980`).

All earlier baseline metrics and calibrations across Phase 3 and Phase 4 were recorded against the earlier build ID `1343d82ebee3` (manifest digest: `sha256:1343d82ebee38e26a4dd6b0180b915eb91550184e67c505dea97509571c8f683`). Specifically:
1. `evals/reports/vision_calibration.json`: Evaluated upload-time vision transcription logprob confidence on `1343d82ebee3`.
2. `evals/reports/phase3_baseline.json`: Evaluated Hit@5, citation precision, faithfulness, and answer generation on `1343d82ebee3`.
3. `evals/reports/phase4_baseline.json`: Evaluated false-fill rate (0.00%) and visual recovery Hit@5 (100.0%) on `1343d82ebee3`.
4. `backend/tests/smoke_phase4_extraction.py`: Verified dual-read compare and field state engine on `1343d82ebee3`.

## Pin Audit & Invariant Locations
An audit across the repository for where model digests and versions are pinned or recorded revealed:
- **Runtime Profiles (`backend/config/profiles/*.yaml`)**: Profiles configure role mappings by model name (`model: "qwen3-vl:4b"` in `desktop-gpu.yaml`), but do not pin an Ollama SHA-256 digest string in the YAML file. (In contrast, embedding and reranker profiles pin exact HuggingFace git commit revisions via `revision: "..."`).
- **Database Observability (`page_readings.model_version`, `run_steps.model_version`)**: In `app/adapters/models/ollama.py`, `OllamaModelProvider._get_model_digest()` queries Ollama's `/api/tags` endpoint dynamically, caching and writing the live manifest digest to `page_readings.model_version` and `run_steps.model_version` (widened to `VARCHAR(255)` in Migration 0011).
- **Unit Test Mocks (`backend/tests/unit/test_ollama_provider.py`)**: `EXPECTED_DIGEST` pinned the mock digest; updated to `sha256:a1b29c246319cdcb1f3842f36d29f198f737c0cf516ba1bcba34b1b974e87980`.
- **Threshold Calibration**: `RELIABLE_TEXT_THRESHOLD = 0.25`, `WEAK_EVIDENCE_THRESHOLD = 0.0`, and OCR/vision confidence threshold `0.70` are preserved unchanged until empirical re-measurement is conducted.

## Decision Outcome
1. **Record the Build Update**:
   - Upstream Ollama build ID `a1b29c246319` (`sha256:a1b29c246319cdcb1f3842f36d29f198f737c0cf516ba1bcba34b1b974e87980`) is formally recorded as the active local `qwen3-vl:4b` build.
2. **Preserve Thresholds**:
   - Thresholds are intentionally kept unchanged (`0.70` OCR/vision fallback gate; `0.25` reranker gate) to prevent premature tuning without data.
3. **Mandatory Re-Measurement List**:
   The following benchmarks and verifications must be re-run on this build before accepting new baseline claims:
   - **Vision Confidence Calibration**: Re-run `backend/scripts/calibrate_vision_threshold.py` across golden `VISUAL_RECOVERY` pages (`clean_fax.pdf`, `skewed_fax.pdf`, `faint_fax_2page.pdf`, `patient_intake_form.pdf`) to determine geometric mean token logprob confidence distribution on the new weights.
   - **Reranker-Gated Answers**: Verify that answer generation across all answer modes (`exact`, `short`, `explain`, `table`) maintains byte-for-byte citation verbatim accuracy, prompt token ceilings, and refusal adherence.
   - **Phase 3 Smoke Pipeline**: Re-run `backend/tests/step4_smoke_run.py` against the 11 benchmark smoke questions on `desktop-gpu` to verify end-to-end question answering and conflict presentation.
   - **Phase 4 Extraction Evaluation**: Re-run `backend/scripts/run_phase4_extraction_eval.py` and `backend/tests/smoke_phase4_extraction.py` to confirm:
     - False-fill rate on unreadable fields remains strictly `0.00%` (Invariant 6).
     - Visual recovery Hit@5 meets or exceeds `90.0%`.
     - Dual-read reconciliation between EasyOCR and Ollama vision produces valid field states without unreadable fields receiving guesses.

## Verification
- Verified local Ollama model list reports ID `a1b29c246319` for `qwen3-vl:4b`.
- Verified manifest digest `sha256:a1b29c246319cdcb1f3842f36d29f198f737c0cf516ba1bcba34b1b974e87980` in `cka-data/ollama/manifests/registry.ollama.ai/library/qwen3-vl/4b`.
- Updated mock digest in `backend/tests/unit/test_ollama_provider.py`; 18/18 tests pass.

# ADR-0018: Question-Time Vision Reading (Node 6 vision_read), 6 GB VRAM Image Scaling, and Dual Fallback

- **Status**: Accepted
- **Date**: 2026-10-21
- **Author**: Antigravity / Srinath Gollapally

## Context & Problem Statement
In Phase 4 Task 4.1, the query pipeline encounters pages that require visual inspection (Node 5 `assess_evidence` sets `evidence_state="VISUAL_REQUIRED"`).
Per the architecture specification lines 198 and 296:
1. "Node 6 `vision_read`: Sends the page image and the question to the vision role -> Node 7 `generate`. If it fails: use OCR text if its confidence passes, otherwise refuse for that page."
2. "Desktop GPU, the default for real-model work. Office desktop: i7-12700F, 32 GB RAM, RTX 3050 with 6 GB. Profile `desktop-gpu`: one model, `qwen3-vl:4b`, for both the answer and vision roles, so nothing swaps inside 6 GB of VRAM; embeddings and reranker run on CPU. Scale page images down before vision reads to keep memory in bounds."
3. Invariant 5: *"Visual readings are derived evidence: always cited as visual, shown with the page image, never treated as equal to original text."*

## Decision Drivers
- **6 GB VRAM Hardware Budget**: High-resolution scanned faxes (e.g. 2400x3200) can cause Out-Of-Memory (OOM) failures or swap churn on the RTX 3050 6 GB GPU if passed unscaled.
- **Pure Core Boundaries**: `backend/app/core/` must never import vendor imaging libraries like PIL/Pillow directly (Rule 4, verified by AST boundary invariants).
- **Dual Fallback Policy**: If the vision model times out or encounters an error, the pipeline must fall back to stored OCR text if OCR confidence is `>= 0.70`; if OCR confidence is `< 0.70` (or absent), it must refuse cleanly without hallucinating or guessing.
- **Zero-Content Checkpoints**: Checkpointed graph state must store control fields only (`vision_status`, `needs_vision`), keeping raw image bytes and question-time transcripts in `GraphRuntimeContext`.

## Decision Outcome
1. **Pre-Scaling & VRAM Protection**:
   - `ImageProcessor` port interface is wired into `GraphPorts` (`ports.image_processor`).
   - Before invoking multimodal vision generation, `node_vision_read` (and `OllamaModelProvider.read_page`) scales the page image using `prepare_image_for_vision(image_bytes, max_dimension=1024, max_bytes=4*1024*1024)`.
   - The scaling operates through `PillowImageProcessor` in `app/adapters/images/pillow.py`, keeping `app/core/` completely pure and vendor-free.
2. **`ModelProvider.read_page` Implementation**:
   - `OllamaModelProvider.read_page(role=ModelRole.VISION, image=image, question=question)` injects the user question into the `vision_read_page` template from `prompts/registry.yaml`.
   - Thinking tags (`<think>...</think>`) are stripped from `qwen3-vl:4b` outputs, and confidence is computed from token logprobs.
3. **Dual Fallback & Routing in Node 6**:
   - If `read_page` succeeds, `node_vision_read` puts the reading in `ctx.evidence_text`, sets `ctx.candidate_evidence_types[key] = "visual"`, and returns `{"vision_status": "ok", "needs_vision": True}`.
   - If `read_page` fails and OCR confidence is `>= 0.70`, it engages OCR fallback, returning `{"vision_status": "ocr_fallback", "needs_vision": True}` to allow Node 7 `generate` to speak from the digital/OCR layer.
   - If `read_page` fails and OCR confidence is `< 0.70`, it sets `ctx.response_text = "I couldn't read the scanned page clearly enough to answer."` and returns `{"vision_status": "unusable", "status": "refusal", "needs_vision": True, "evidence_ids": []}`, which conditional edge `_route_from_vision_read` routes straight to Node 9 `respond`.
4. **Invariant 5 Compliance**:
   - Node 7 `generate` checks `if state.get("needs_vision"): ev_type = "visual"`. The generated answer structured payload strictly carries `evidence_type="visual"`.

## Verification
- Proved by `backend/tests/integration/test_vision_read_node.py` (6 integration tests passed):
  - Question and image delivery to vision model.
  - Image pre-scaling to `<= 1024px` dimension and `<= 4 MB`.
  - OCR fallback when OCR confidence is `>= 0.70`.
  - Refusal when OCR confidence is `< 0.70`.
  - Invariant 5 `evidence_type='visual'` propagation through full graph.
  - Telemetry tracing across start and end.
- Proved by unit test suite `backend/tests/unit/test_ollama_provider.py::test_read_page_success`.
- Proved by architecture invariant suite `backend/tests/invariants/test_architecture_boundaries.py` (24 boundary tests passed).

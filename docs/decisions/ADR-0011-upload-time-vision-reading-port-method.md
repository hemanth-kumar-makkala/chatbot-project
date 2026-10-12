# ADR-0011: Multimodal Vision Port Method for Upload-Time Transcription

- **Status**: Accepted
- **Date**: 2026-10-09
- **Author**: Antigravity / Srinath

## Context & Problem Statement
In Task 2.5, upload-time background ingestion requires multimodal visual transcription of scanned and image-heavy pages via `ModelProvider`. The resulting text is indexed in `page_readings` (`source="vision_ingest"`) and chunked with visual evidence lineage.

The existing `ModelProvider` port interface defined a single multimodal method:
```python
async def read_page(
    self,
    role: ModelRole,
    image: bytes,
    question: str,
) -> PageReading: ...
```

This signature was designed for question-time visual reading (Phase 4, Node 6), where a user question guides the visual inspection. At document upload time (Phase 2), there is no question; the pipeline requires full visual transcription of all visible text, tables, stamps, and layout on the page.

We must decide whether to make `question` optional in `read_page` or provide a dedicated method on `ModelProvider`.

## Decision Drivers
- **Interface Segregation**: Clients calling upload-time transcription should not have to provide or handle question arguments.
- **Type Safety**: Avoid `question: str | None = None` and runtime `if question is None:` branching inside model adapters.
- **Fail-Safe Contract**: Question-time visual reads must never accidentally omit a question or fall back to an unprompted full-page transcription.
- **Invariant 5**: *"Visual readings are derived evidence: always cited as visual, shown with the page image, never treated as equal to original text."*

## Considered Options
1. **Option 1: Make `question` Optional (`question: str | None = None`)**:
   - Reuse `read_page` for both upload-time and question-time reads.
   - *Downside*: Ambiguous contract. Every provider adapter must branch on `if question is None:`. Callers at question time can accidentally omit the question without a type-check error.
2. **Option 2: Dedicated `transcribe_page` Method (Chosen)**:
   - Add `async def transcribe_page(self, role: ModelRole, image: bytes) -> PageReading:` to `ModelProvider`.
   - Keep `read_page(self, role: ModelRole, image: bytes, question: str) -> PageReading` strictly for question-time targeted inspection.

## Decision Outcome
Adopt **Option 2**.

1. `ModelProvider` provides two distinct visual methods:
   - `transcribe_page(role: ModelRole, image: bytes) -> PageReading`: Full multimodal transcription of a page image at upload time.
   - `read_page(role: ModelRole, image: bytes, question: str) -> PageReading`: Question-directed visual inspection at query time.
2. In the `MockModelProvider`, `transcribe_page` returns deterministic mock visual transcription text with `model_id="mock-vision"`.
3. In production adapters (`OllamaModelProvider`, `VertexModelProvider`), `transcribe_page` sends an unprompted transcription system prompt, while `read_page` injects the user question into the prompt template.

## Confidence Score Contract in PageReading
`PageReading.confidence` is typed as optional (`confidence: float | None = None`).
- **Rationale**: Downstream multimodal visual transcription providers (e.g., local Ollama vision models) do not reliably return calibrated scalar token confidence scores without custom logprob parsing.
- **Fail-Safe Policy**: Any `PageReading` where `confidence is None` (or `math.isnan(confidence)`) is strictly treated as *not confident* by `vision_policy.py` (`VisionReadingOutcome.needs_review = True`, `review_reason = "vision_confidence_missing"`, `is_indexable = False`). Non-empty text alone never counts as success when confidence is missing.
- **Audit Confirmation**: Every caller inspecting `PageReading.confidence` treats `None` as unconfident.

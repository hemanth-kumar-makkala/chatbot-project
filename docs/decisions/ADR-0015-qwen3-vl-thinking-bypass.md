# ADR-0015: Qwen3-VL Thinking Mode Bypass via Assistant Prefill

- **Status**: Accepted
- **Date**: 2026-10-20
- **Author**: Hemanth Kumar Makkala / Antigravity

## Context & Problem Statement
On the local `desktop-gpu` deployment profile, Ollama serves `qwen3-vl:4b` for query routing and answer generation. The model's default chat template incorporates an internal reasoning chain-of-thought phase (`<think>...</think>`). For typical retrieval queries with evidence context (500–1500 tokens), the model generates ~2,500 thinking tokens before beginning structured JSON generation. On an RTX 3050 6GB GPU, this thinking phase incurs 45–60 seconds of latency per turn, exceeding the Next.js development server proxy timeout (30 seconds) and degrading interactive chat usability.

## Decision Drivers
- Response latency: real-time staff chat queries must complete within interactive timeframes (<5 seconds).
- Proxy timeouts: Next.js dev server rewrites drop socket connections taking >30s with HTTP 500.
- Token ceilings: prevent generation loops from exhausting token limits on thinking rather than content.
- Clean answers: zero thinking tokens or `<think>` tags must ever leak into user-facing answers or confidence scores.

## Considered Options
1. **Ollama native `think: False` parameter**:
   Tested in foreground against Ollama `qwen3-vl:4b` (`POST /api/chat` with `"think": False`). The model's chat template still emits a thinking block (`thinking: '<think>...'`), which does not suppress chain-of-thought token generation in this model release.
2. **Assistant message prefill (`{"role": "assistant", "content": "</think>"}`)**:
   By appending an assistant message `</think>` to the prompt, the model treats the thinking phase as already concluded. It emits 0 thinking tokens, starts immediate structured JSON generation, and reduces turn latency from ~65 seconds down to ~1.16–4.10 seconds.
3. **Instructional suppression (`/no_think` or system prompt)**:
   Prompt instructions reduced thinking length slightly (~270 tokens), but did not reliably eliminate the 40+ second delay under long multi-chunk evidence contexts.

## Decision Outcome
Chosen option: **Option 2 (Assistant message prefill `</think>`)**, combined with explicit `max_tokens` ceilings (`150` for router, `512` for answer generation).
Additionally, `OllamaModelProvider` regex strips any residual `<think>[\s\S]*?</think>` blocks from raw content before parsing.

### Consequences & Trade-offs
- **Positive**: Query latency drops from ~65s to 1–4s; zero proxy timeout drops; structured JSON generation starts immediately.
- **Negative / Risks**: Relies on model accepting assistant prefill syntax.
- **Mitigations**: Supported natively by Ollama's OpenAI-compatible and Ollama chat APIs; verified by automated unit tests asserting that no thinking text leaks to answer content or confidence scores.

## Compliance & Invariant Verification
- Verified by `backend/tests/unit/test_ollama_generate.py` asserting `<think>` is not in output text.
- Verified by real call benchmarks on `desktop-gpu` profile.

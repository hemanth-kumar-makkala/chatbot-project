# ADR-0017: Server-Sent Events (SSE) Streaming API and Tenant-Scoped Conversation Routes

- **Status**: Accepted
- **Date**: 2026-10-21
- **Author**: Antigravity / Srinath Gollapally

## Context & Problem Statement
Phase 3 Task 3.6 requires real-time streaming of pipeline status lines, tokens, verified answers, citations, and error references to the chat UI.
Additionally:
1. Product owner decision (recorded in `docs/ui/UI_INVENTORY.md` line 391 and `docs/ui/CONNECTION_MAP.md` line 85) mandated that the synchronous JSON endpoint `POST /v1/questions` remains fully intact (used by smoke tests, batch evals, and non-streaming clients), with streaming introduced as a dedicated endpoint (`POST /v1/questions/stream`).
2. Staff consultation transcripts, message histories, and conversation clearing routes (`GET /v1/assistants/{id}/conversations`, `GET /v1/conversations/{id}/messages`, `DELETE /v1/assistants/{id}/conversations`, `DELETE /v1/conversations/{id}`) were needed under Row-Level Security (RLS) and Invariant 2 & 3 (strict anti-probing and tenant isolation).

## Decision Drivers
- **Real-Time Responsiveness**: Staff see immediate feedback ("Searching documents...", "Page N is a scanned image...", "Generating answer from validated evidence...") rather than waiting silently.
- **Backwards Compatibility**: Keep `POST /v1/questions` intact so benchmarks, evaluation harnesses, and headless clients run without changes.
- **Tenant Isolation & Anti-probing**: Tenant context is derived strictly from assistant or session authentication, never accepted from user input; cross-tenant access returns 404.
- **Connection Resource Protection**: PostgreSQL transactions are not held idle during long SSE token streams; user message is committed prior to streaming and final answer message/audit events are committed upon graph completion.

## Decision Outcome
1. **Dedicated SSE Streaming Endpoint (`POST /v1/questions/stream`)**:
   - Built on FastAPI `StreamingResponse` using media type `text/event-stream`.
   - Streams sequential event types:
     - `event: status`: Pipeline node transition announcements (`step: "retrieve"`, `"assess_evidence"`, `"vision_read"`, `"generate"`, `"validate"`).
     - `event: token`: Streamed token chunks for real-time text rendering.
     - `event: answer`: Structured payload containing verified answer text, answer mode, evidence state, citations, and public error references.
     - `event: citation`: Real source chips and conflict sources resolved from PostgreSQL under tenant session.
     - `event: done`: Carries `request_id`, `conversation_id`, and `message_id`.
     - `event: error`: On graph failure or internal exception, returns random `ERR-XXXXXX` public error reference without leaking stack traces or internal details.
2. **Conversation & Transcript Routes (`backend/app/api/routes/conversations.py`)**:
   - `GET /v1/assistants/{assistant_id}/conversations`: Lists conversations and message counts for the assistant under tenant RLS.
   - `GET /v1/conversations/{conversation_id}/messages`: Streams message transcripts for the conversation, searching across user's authorized tenants under RLS.
   - `DELETE /v1/assistants/{assistant_id}/conversations`: Deletes conversations for the assistant under tenant RLS.
   - `DELETE /v1/conversations/{conversation_id}`: Deletes a single conversation.

## Consequences & Verification
- Both `POST /v1/questions` and `POST /v1/questions/stream` operate seamlessly side-by-side.
- Verified by integration test suite `backend/tests/integration/test_sse_streaming.py` across 4 test suites:
  - Event stream formatting and status lines.
  - Safe error event emission with random error references.
  - Cross-tenant streaming isolation.
  - Full CRUD and cross-tenant isolation on conversation routes.

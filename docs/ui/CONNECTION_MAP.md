# Backend Connection Map: Complete UI to Engine Mapping

Source of truth: `Client Knowledge Assistant Architecture and Build Plan.md`, `docs/phases/PHASE-0.md` through `PHASE-7.md`, `docs/ui/UI_INVENTORY.md`, `docs/graph.md`, `docs/OPEN_QUESTIONS.md`, `docs/phases/PHASE-1-GATE.md`, `docs/decisions/ADR-0002-clarify-topics-and-extraction-subgraph.md`, `backend/app/api/routes/`, and `web/lib/api.ts`.

---

## 1. Overview & Connection Status Definitions

This document maps every UI feature across all three roles (Platform Admin, Client Admin, Staff) to its corresponding API route, backend engine component, mock profile status, and requirements for real production ML models.

### Status Categories (Strict Invariants)
- **`connected and working`**: The API route exists, is wired in the frontend (`web/lib/api.ts`), is actively invoked by the UI, and has verified passing integration/unit tests on the `mock` profile.
- **`UI renders it; backend path exists; not reachable from the UI on mock`**: The UI contains active rendering code for the card/state, the backend graph/route execution path exists and is proven by unit/integration tests, but live user queries on the mock profile do not reach this state because the mock endpoint hardcodes normal evidence.
- **`not rendered by UI yet; backend path exists in graph`**: The backend graph execution path exists and passes automated tests, but the chat frontend does not yet render specialized UI treatment for this response.
- **`partly built`**: The UI renders part of the element (e.g. text badge), but other essential visual elements (e.g. image thumbnail or topic chips) or backend paths are not yet connected.
- **`backend missing`**: The UI requires an endpoint or schema that is not yet exposed in `backend/app/api/routes/`, even if underlying database models or ports exist.

---

## 2. Complete Connection Map Table

| UI Element & Screen | API Route | Backend Component | Current Status (Mock Profile) | Proving Code / Test | Needed for Real Models (with Doc & Section Citations) |
|---|---|---|---|---|---|
| **Login Screen Form**<br>`UI-SCR-01` (`/login`) | `POST /v1/auth/login` | `auth.py:login`<br>• `MockAuthProvider`<br>• `LoginRateLimiter` | **connected and working** | `backend/tests/integration/test_api_auth_endpoints.py`, `UI_TEST_CHECKLIST.csv` UI-001 Pass | Auth is model-independent. Production auth is undecided: built-in or Identity Platform, with SSO listed as a later option (spec sections "Core principle: a fixed engine with swappable parts" and "Risks and open decisions") |
| **Workspace Identity & Nav**<br>`UI-SCR-02` (`AppShell`) | `GET /v1/auth/me` | `auth.py:get_current_user_info`<br>• `User` DB query via `unscoped_session` | **connected and working** | `backend/tests/integration/test_api_auth_endpoints.py`, mounted in `web/components/AppShell.tsx` | None (not specified in docs as needing models) |
| **Assistants Grid**<br>`UI-SCR-03` (`/assistants`) | `GET /v1/assistants` | `backend missing`<br>• Live mode renders seeded Clinic A assistant (`DEV_DEFAULT_ASSISTANT_ID`) | **partly built** *(connected in demo mode; live renders seeded card)* | `web/__tests__/shell.test.tsx`, `UI_TEST_CHECKLIST.csv` UI-041/UI-054 | Relational discovery endpoint `GET /v1/assistants` for live switching |
| **New Assistant Dialog**<br>`UI-SCR-03` (`/assistants`) | `POST /v1/assistants` | `backend missing` | **Not connected yet** *(shows route disclaimer)* | `web/__tests__/shell.test.tsx` | Relational CRUD endpoint |
| **Delete Assistant Action**<br>`UI-SCR-03` (`/assistants`) | `DELETE /v1/assistants/{id}` | `backend missing` | **Not connected yet** *(shows route disclaimer)* | `web/__tests__/shell.test.tsx` | Relational CRUD endpoint |
| **Workspace Settings: General**<br>`UI-SCR-04` (`/settings`) | `GET /v1/auth/me` | `auth.py:get_current_user_info`<br>• Displays read-only tenant name | **connected and working** | `web/__tests__/shell.test.tsx` | None |
| **Workspace Settings: Members**<br>`UI-SCR-04` (`/settings`) | `GET /v1/tenant/members` | `backend missing` | **Not connected yet** *(shows route disclaimer)* | `web/__tests__/shell.test.tsx` | Tenant membership directory endpoint |
| **Assistant Home (Chat)**<br>`UI-SCR-05` (`/assistants/[id]`) | `POST /v1/questions` | `questions.py:ask_question`<br>• LangGraph `build_app_graph()` | **connected and working** | `backend/tests/integration/test_graph_end_to_end.py:46`, `web/__tests__/chat.test.tsx`, `web/__tests__/shell.test.tsx` | Connected with BGE-M3 retrieval, BGE-reranker-v2-m3, Ollama `qwen3-vl:4b` structured generation, exact quote validation, and evidence gate (Task 3.1-3.5) |
| **New Chat Action**<br>`UI-SCR-05` (`/assistants/[id]`) | Client state reset | Client state | **connected and working** | `web/__tests__/shell.test.tsx` | None (always resets conversation thread to empty) |
| **Data Sources: Files Upload**<br>`UI-SCR-06` (`/data-sources`) | `POST /v1/assistants/{id}/knowledge/upload` | `knowledge.py:upload_document`<br>• `UploadService`<br>• `split_document_job` | **connected and working** | `backend/tests/integration/test_upload_service.py`, `UI_TEST_CHECKLIST.csv` UI-007 to UI-011 Pass | Malware scanner undecided; currently mock scanner (`MockMalwareScanner`) |
| **Data Sources: Text Snippet Card**<br>`UI-SCR-06` (`/data-sources`) | `POST /v1/assistants/{id}/knowledge/snippets` | `backend missing` | **Not connected yet** *(shows "Coming later")* | `web/__tests__/shell.test.tsx` | Text snippet ingestion pipeline |
| **Data Sources: Q&A Card**<br>`UI-SCR-06` (`/data-sources`) | `POST /v1/assistants/{id}/knowledge/qa` | `backend missing` | **Not connected yet** *(shows "Coming later")* | `web/__tests__/shell.test.tsx` | Q&A pair ingestion pipeline |
| **Data Sources: Documents Table**<br>`UI-SCR-06` (`/data-sources`) | `GET /v1/assistants/{id}/knowledge/documents` | `knowledge.py:list_documents`<br>• `PostgresDocumentRepository.list_documents_with_status` | **connected and working** | `backend/tests/integration/test_api_knowledge_endpoints.py`, `UI_TEST_CHECKLIST.csv` UI-005 Pass, UI-015 Pass | None |
| **Data Sources: Retire Document**<br>`UI-SCR-06` (`/data-sources`) | `DELETE /v1/assistants/{id}/knowledge/documents/{doc_id}` | `knowledge.py:retire_document`<br>• `PostgresDocumentRepository.retire_document` | **connected and working** | `backend/tests/integration/test_api_knowledge_step2.py`, `UI_TEST_CHECKLIST.csv` UI-016 Pass | None |
| **Data Sources: Review Drawer**<br>`UI-SCR-06` (`/data-sources`) | `GET .../documents/{ver_id}/review`<br>`POST .../pages/{p}/accept`<br>`POST .../pages/{p}/exclude`<br>`POST .../promote` | `knowledge.py`<br>• Flagged page review and promotion | **connected and working** | `backend/tests/integration/test_api_knowledge_step2.py`, `web/components/ReviewDrawer.tsx` | Desktop-GPU profile OCR reading to produce real low-confidence flagged pages |
| **Instructions Form**<br>`UI-SCR-07` (`/instructions`) | `PATCH /v1/assistants/{id}/instructions` | `backend missing`<br>• Fixed rule note displayed: "Instructions shape tone and scope. Answers still come only from your documents." | **Not connected yet** *(shows route disclaimer)* | `web/__tests__/shell.test.tsx` | Prompt template customization without overriding Invariant 4 |
| **Guardrails Screen**<br>`UI-SCR-08` (`/guardrails`) | `PATCH /v1/assistants/{id}/guardrails` | `backend missing`<br>• 5 built-in safety rules shown locked on | **Not connected yet** *(shows route disclaimer)* | `web/__tests__/shell.test.tsx` | Configurable blocked topics & custom refusal message |
| **Actions Placeholder**<br>`UI-SCR-09` (`/actions`) | None (placeholder) | UI placeholder<br>• "Coming soon. Actions that call other systems need a separate security review." | **connected and working** | `web/__tests__/shell.test.tsx` | Tool call execution framework with strict tenant isolation and BAA compliance |
| **Conversations List & Viewer**<br>`UI-SCR-10` (`/conversations`) | `GET /v1/assistants/{id}/conversations` | `backend missing`<br>• Demo mode renders fixtures; Live mode shows route disclaimer | **Not connected yet** *(shows route disclaimer in live)* | `web/__tests__/shell.test.tsx` | Conversation persistence and retrieval endpoints |
| **Assistant Settings: General**<br>`UI-SCR-11` (`/settings`) | `PATCH /v1/assistants/{id}` | `backend missing`<br>• Displays assistant name, ID copy button, description | **Not connected yet** *(shows route disclaimer)* | `web/__tests__/shell.test.tsx` | Relational update endpoint |
| **Assistant Settings: Danger Zone**<br>`UI-SCR-11` (`/settings`) | `DELETE /v1/assistants/{id}/conversations`<br>`DELETE /v1/assistants/{id}` | `backend missing`<br>• Confirm dialogs rendered | **Not connected yet** *(shows route disclaimer)* | `web/__tests__/shell.test.tsx` | Bulk deletion & assistant purge endpoints |
| **Legacy Chat Route Redirect**<br>`UI-SCR-12` (`/chat`) | Client redirect | Redirects to `/assistants/[id]` (Home) | **connected and working** | `web/__tests__/shell.test.tsx` | None |
| **Legacy Knowledge Route Redirect**<br>`UI-SCR-12` (`/knowledge`) | Client redirect | Redirects to `/assistants/[id]/data-sources` | **connected and working** | `web/__tests__/shell.test.tsx` | None |
| **Grounded Answer Card & Citations**<br>`UI-SCR-05` (`ChatPage`) | `POST /v1/questions` (response `sources`) | `questions.py:ask_question`<br>• LangGraph Node 7 / Node 9 + `SourceCitation` schemas | **connected and working** | `backend/tests/integration/test_graph_end_to_end.py:46`, `web/__tests__/chat.test.tsx:48` | Real chunk source metadata resolution from `chunks` table in Postgres |
| **Visual Answer Badge with Thumbnail**<br>`UI-SCR-05` (`ChatPage`) | `POST /v1/questions` (`evidence_type="visual"`) + `GET .../pages/{p}/image` | `ChatPage`<br>• Rendered PNG page thumbnails; click opens SourcePanel | **partly built** *(connected in demo mode)* | `web/__tests__/ui3.test.tsx`, `UI_TEST_CHECKLIST.csv` UI-047 Pass | PHASE-4 Task 4.2: Grounded answer presentation with page thumbnail linking to full-page view |
| **Embedded PDF.js Viewer**<br>`UI-SCR-05` (Side Panel) | `GET /v1/assistants/{id}/knowledge/documents/{version_id}/file` | `knowledge.py:get_document_file`<br>• Streams stored PDF to `SourcePanel.tsx` with staff/admin tenant-scoped access | **connected and working** | `web/__tests__/ui3.test.tsx`, `backend/tests/integration/test_staff_source_routes.py` | Tenant-scoped staff-allowed document file streaming route connected to `SourcePanel.tsx` |
| **Refusal Presentation**<br>`UI-SCR-05` (`ChatPage`) | `POST /v1/questions` (`status="refusal"`) | `questions.py` -> LangGraph Node 5 / Node 7 | **UI renders it; backend path exists; not reachable from the UI on mock** | `backend/tests/integration/test_graph_end_to_end.py:155`, `web/__tests__/chat.test.tsx:127` | Task 3.3: Evidence assessment threshold in Node 5 & Ollama refusal generation in Node 7 |
| **Conflict Warning Banner**<br>`UI-SCR-05` (`ChatPage`) | `POST /v1/questions` (`status="conflicting"`) | `questions.py` -> LangGraph Node 5 | **UI renders it; backend path exists; not reachable from the UI on mock** | `backend/tests/integration/test_graph_end_to_end.py:136`, `web/__tests__/chat.test.tsx:162` | Task 3.3: Cross-document contradiction detection logic in Node 5 |
| **Clarification Needed Card & Topics**<br>`UI-SCR-05` (`ChatPage`) | `POST /v1/questions` (`status="clarify"`) | `questions.py` -> LangGraph Node 2 | **UI renders it; backend path exists; not reachable from the UI on mock** | `backend/tests/integration/test_graph_end_to_end.py:99`, `web/__tests__/chat.test.tsx:302` | PHASE-1 Task 1.6, OPEN_QUESTIONS 1.6, and ADR-0002 |
| **General Task Draft Presentation**<br>`UI-SCR-05` (`ChatPage`) | `POST /v1/questions` (`route="general"`) | `questions.py` -> LangGraph Node 2 -> Node 7 | **UI renders it; backend path exists; not reachable from the UI on mock** | `backend/tests/integration/test_graph_end_to_end.py:83`, `web/__tests__/ui2.test.tsx` | General task routing in Node 2 and ungrounded generation using `qwen3-vl:4b` in Node 7 |
| **Safe Failure Card (`ERR-xxxx`)**<br>`UI-SCR-05` (`ChatPage`) | `POST /v1/questions` (`status="safe_failure"`) | `questions.py` -> LangGraph safe failure node | **UI renders it; backend path exists; not reachable from the UI on mock** | `backend/tests/integration/test_graph_end_to_end.py:170`, `web/__tests__/chat.test.tsx:197` | Operational logging correlation ID generator |
| **Session Document Upload**<br>`UI-SCR-05` (Chat Input) | `none yet`<br>*(Needs `POST /v1/conversations/{id}/documents`)* | `ChatPage`<br>• Golden fax picker & session banner in demo; live route disclaimer | **partly built** *(connected in demo mode; live needs Phase 4 Task 4.3 route)* | `web/__tests__/ui4.test.tsx`, `UI_TEST_CHECKLIST.csv` UI-034 Pass | PHASE-4 Task 4.3 (session document upload); prompt injection guardrails are PHASE-5 Tasks 5.1/5.2 |
| **Dual-Read Extraction Table**<br>`UI-SCR-05` (Side-by-Side) | `none yet`<br>*(Needs `GET .../extractions/{doc_id}`)* | `ChatPage` / `ExtractionTable.tsx`<br>• Split view: page preview beside field table | **partly built** *(connected in demo mode; live needs extraction route)* | `backend/tests/integration/test_graph_end_to_end.py:118`, `web/__tests__/ui4.test.tsx` | Phase 4 Tasks 4.1 to 4.5: `qwen3-vl:4b`, extraction subgraph, field state engine |
| **Feedback Thumbs & Reason**<br>`UI-SCR-05` (`ChatPage`) | `none yet`<br>*(Needs `POST /v1/questions/{message_id}/feedback`)* | `ChatPage`<br>• In-memory store in demo; live route disclaimer | **partly built** *(connected in demo mode; live needs Phase 5 Task 5.5 route)* | `web/__tests__/ui5.test.tsx`, `UI_TEST_CHECKLIST.csv` UI-038 Pass | Relational feedback logging endpoint (PHASE-5 Task 5.5) |
| **Chat Rate Limit Warning**<br>`UI-SCR-05` (`ChatPage`) | `POST /v1/questions` (HTTP 429) | `ChatPage` / `web/lib/api.ts`<br>• Maps HTTP 429 to RATE_LIMIT_EXCEEDED | **UI renders it; backend path exists; mapped in api.ts** | `web/__tests__/ui5.test.tsx`, `UI_TEST_CHECKLIST.csv` UI-039 Pass | PHASE-5 Task 5.1: Per-user and per-tenant rate limits |

---

## 3. Summary of Backend Gaps for Full UI Connectivity

1. **Workspace & Assistant Discovery & Lifecycle (ADR-0014)**:
   - `GET /v1/assistants`: Returns list of assistants accessible to the authenticated tenant user.
   - `POST /v1/assistants`: Creates a new assistant within the client tenant scope.
   - `PATCH /v1/assistants/{id}`: Updates assistant name and description.
   - `DELETE /v1/assistants/{id}`: Deletes assistant and cascades to knowledge documents/chunks.
2. **Assistant Instructions & Guardrails Customization (ADR-0014)**:
   - `PATCH /v1/assistants/{id}/instructions`: Updates assistant prompt tone/scope instructions (cannot override Invariant 4).
   - `PATCH /v1/assistants/{id}/guardrails`: Configures custom blocked topics list and refusal message.
3. **Conversations & Session History (Phase 3 & Phase 4)**:
   - `GET /v1/assistants/{assistant_id}/conversations`: Lists user conversations for active assistant.
   - `DELETE /v1/assistants/{assistant_id}/conversations`: Deletes all conversation records for active assistant.
   - `GET /v1/conversations/{conversation_id}/messages`: Fetches chronological messages for conversation resume.
   - `POST /v1/conversations/{conversation_id}/documents`: Uploads session PHI file into `session_documents` without indexing into shared knowledge.
4. **Data Sources Extensions (ADR-0014)**:
   - `POST /v1/assistants/{id}/knowledge/snippets`: Text snippet ingestion.
   - `POST /v1/assistants/{id}/knowledge/qa`: Q&A pair ingestion.
5. **Workspace Members**:
   - `GET /v1/tenant/members`: Lists members of the authenticated workspace tenant.
6. **Document Byte Streaming & Staff Image Access**:
   - `GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/file`: Streams original PDF bytes under tenant authorization for browser PDF.js rendering.
   - Staff-allowed page image access: Existing `GET .../pages/{page_number}/image` is restricted to `client_admin` in `knowledge.py`. Staff require access to page thumbnails (spec section "The 15 invariants" (Invariant 5)) and source previews (spec section "End result: how people will use it").
7. **SSE Real-Time Streaming (Task 3.6)**:
   - `POST /v1/questions/stream`: Server-Sent Events endpoint streaming pipeline status lines (including Node 6 scanned page announcements) and tokens, keeping the synchronous JSON endpoint (`POST /v1/questions`) intact with an ADR.
8. **Quality & Compliance Feedback (Task 5.5)**:
   - `POST /v1/questions/{message_id}/feedback`: Accepts `{ rating: "up" | "down", reason?: string, comments?: string }` and persists to `feedback` table.
9. **Phase 4 Dual-Read Extraction**:
   - Extraction execution endpoint and structured field state adjudication responses (`FILLED_HIGH`, `CONFLICTING`, `UNREADABLE`, `INVALID`, `NOT_PRESENT`, `NOT_APPLICABLE`).

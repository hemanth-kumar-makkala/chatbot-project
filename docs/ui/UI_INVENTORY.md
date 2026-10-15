# UI Inventory: Complete Frontend Surface Across All Phases

Source of truth: `Client Knowledge Assistant Architecture and Build Plan.md`, `docs/phases/PHASE-0.md` through `PHASE-7.md`, `docs/ui/KNOWLEDGE_PAGE_SPEC.md`, `docs/ui/API_CONTRACT_NOTES.md`, `docs/ui/BUILD_NOTES.md`, `docs/graph.md`, `docs/OPEN_QUESTIONS.md`, and `docs/phases/PHASE-1-GATE.md`.

---

## 1. Architectural Guardrails & Role Principles

1. **No Admin Dashboards Rule**:
   - Architecture Spec (sections "Platform Admin", "End result: how people will use it") and `docs/ui/KNOWLEDGE_PAGE_SPEC.md` (section "Core principles"): **"No admin dashboards. The Knowledge page and its review list are the only admin screens."**
   - The application does not contain generic administrative overview pages, metric graphs, analytics charts, or platform administration dashboards.
2. **Role Capabilities (spec section "End result: how people will use it")**:
   - **Platform Admin (Staffingly)**: Create clients and assistants, manage users, view traces and audit logs (Note: see Section 4 on administrative boundaries).
   - **Client Admin**: Upload, replace (upload newer versions), and retire knowledge files for their client, observe processing progress, review flagged pages, accept/exclude/retry pages, and promote versions.
   - **Staff**: Select assigned client assistants, chat with grounded citations, view cited pages in an embedded PDF viewer, upload session PHI documents for structured field extraction, and provide message feedback.
3. **Security & Privacy Invariants**:
   - Invariant 5: Visual readings are cited as visual evidence and displayed with page thumbnails.
   - Invariant 6: Unreadable extraction fields receive explicit state (`UNREADABLE`), remaining blank and flagged, never guessed.
   - Invariant 7: PHI uploaded in chat sessions stays strictly in `session_documents` and never enters the shared knowledge index.
   - Cookie-only sessions: Session tokens are strictly HttpOnly cookies; no tokens stored in client storage. Double-submit `X-CSRF-Token` required on state mutations.

---

## 2. Complete Inventory by Phase, Screen, and State

### Phase 1: Authentication & Layout Foundation

#### UI-SCR-01: Login Screen (`/login`)
- **Target Roles**: Platform Admin, Client Admin, Staff
- **Phase**: Phase 1 (Engine / Auth) & Phase 2 (Task 2.8b)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Query flow" (Node 1), "Security, HIPAA and guardrails", "End result: how people will use it" (Roles), "Swappable components and vendor isolation" (production auth undecided: built-in or Identity Platform, with SSO listed as a later option)
  - `docs/ui/KNOWLEDGE_PAGE_SPEC.md`: Section "Screens" item 1
  - `docs/ui/BUILD_NOTES.md`: Section 1 "Step 4: Foundations & Authentication"
  - `docs/decisions/ADR-0007-session-auth-and-csrf.md`: Section 3
- **What the User Sees and Does**:
  - Sees Staffingly brand gradient background (`#0037B2` to `#A941C6`), centered authentication card, wordmark, email and password inputs, sign-in button, and accessible error banners.
  - Submits valid credentials: sets HttpOnly `session_token` cookie and `csrf_token` cookie; automatically redirects to target assistant page or default `/assistants/[assistantId]/knowledge`.
  - Submits invalid credentials: sees masked error `"Invalid email or password. Please check your credentials."`.
  - Rate limited (5 failed attempts within 60s): sees `"Too many failed login attempts. Please wait before retrying."` (HTTP 429).
  - Unauthenticated access to protected routes redirects to `/login?redirect=...`.
- **Status**: **Built** (`web/app/login/page.tsx`; verified by unit test `foundations.test.ts` and `UI_TEST_CHECKLIST.csv` UI-001 Pass).

#### UI-SCR-02: Top Navigation Bar (`TopBar`)
- **Target Roles**: Platform Admin, Client Admin, Staff
- **Phase**: Phase 1, Phase 2 (Task 2.8b), Phase 3 (Task 3.7)
- **Source Doc & Section**:
  - `docs/ui/KNOWLEDGE_PAGE_SPEC.md`: Section "Screens" item 2
  - `docs/ui/BUILD_NOTES.md`: Section 1 "Step 4: Foundations & Authentication"
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it"
- **What the User Sees and Does**:
  - Fixed top bar with Staffingly gradient, white "Staffingly" wordmark, client/assistant indicator.
  - Navigation tab links toggling between **Knowledge** (`/assistants/[assistantId]/knowledge`) and **Chat** (`/assistants/[assistantId]/chat`).
  - User profile menu displaying logged-in email and Logout button.
  - Clicking Logout calls `POST /v1/auth/logout` with CSRF token, revokes session, clears cookies, and redirects to `/login`.
- **Status**: **Built** (`web/components/TopBar.tsx`; verified by unit test `foundations.test.ts`).

---

### Phase 2: Ingestion & Knowledge Administration

#### UI-SCR-03: Knowledge Page File List (`/assistants/[assistantId]/knowledge`)
- **Target Roles**: Client Admin
- **Phase**: Phase 2 (Task 2.8b)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A client admin's flow"), "Ingestion pipeline"
  - `docs/phases/PHASE-2.md`: Task 2.8
  - `docs/ui/KNOWLEDGE_PAGE_SPEC.md`: Section "Screens" item 2
- **What the User Sees and Does**:
  - Document table displaying Title, Version number, Status Chip, Uploaded Date, and Action buttons (Review, Retire).
  - Empty state when no documents exist: friendly prompt to upload practice guidelines.
  - Polling engine: automatically polls `GET .../knowledge/documents` every 5 seconds while any file is in a non-final state (`Queued`, `Reading`, `Scanning images`); halts polling when all files reach terminal states (`Ready`, `Ready (N pages need review)`, `Failed`, `Retired`) or when the browser tab is hidden (`visibilitychange`).
- **Status**: **Built** (`web/app/assistants/[assistantId]/knowledge/page.tsx`, `web/components/DocumentsTable.tsx`; verified by `knowledge_page.test.tsx` and UI-005 Pass).

#### UI-SCR-04: Document Upload & Newer Version Upload Area
- **Target Roles**: Client Admin
- **Phase**: Phase 2 (Task 2.2, Task 2.8b)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A client admin's flow" steps 2 & 4: *"Uploads a newer version of a document. The old version remains active until the new version is verified. Once verified, the new version becomes active and the old version is archived."*), "Open decisions" (malware scanner undecided: OPEN_QUESTIONS 2.2)
  - `docs/phases/PHASE-2.md`: Task 2.2, Task 2.8
  - `docs/ui/KNOWLEDGE_PAGE_SPEC.md`: Section "Screens" item 2
- **What the User Sees and Does**:
  - Drag-and-drop target box with browse button, indicating accepted formats (PDF, XLSX, CSV) and 50 MB size limit.
  - Client-side validation: instantly rejects files > 50 MB or unsupported extensions (`.docx`, `.zip`) before uploading.
  - Uploading a newer version of an existing document: user selects target document to replace; system uploads new version (`v2`, `v3`) which enters processing while the prior active version remains searchable until the new version reaches `Ready` or is promoted.
  - Duplicate detection banner: if file content matches an existing active version, displays notification that the document is already active.
  - Re-upload after retirement: re-uploading identical bytes after retiring the previous version creates a new document (verified in migration 0010 tests).
- **Status**: **Built** (`web/components/UploadArea.tsx`; verified by UI-007 through UI-011 Pass, `test_upload_dedup_rules.py`).

#### UI-SCR-05: Document Status Chips & Lifecycle Indicators
- **Target Roles**: Client Admin
- **Phase**: Phase 2 (Task 2.8b)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Ingestion pipeline"
  - `docs/phases/PHASE-2.md`: Phase 2 Scope (derived UI states), Task 2.8
  - `docs/ui/KNOWLEDGE_PAGE_SPEC.md`: Section "Status chips"
- **What the User Sees and Does**:
  - Accessible, WCAG AA contrast badges indicating current document version state:
    - `Queued`: neutral grey
    - `Reading`: in-progress blue
    - `Scanning images`: in-progress light blue
    - `Ready`: success green
    - `Ready (N pages need review)`: lavender background with navy text (`--color-lavender`; PHASE-2 Scope, Spec section "Ingestion pipeline")
    - `Failed`: error red
    - `Retired`: subdued grey
- **Status**: **Built** (`web/components/StatusChip.tsx`, `web/lib/statusChips.ts`; verified by `knowledge_page.test.tsx` and UI-015 Pass).

#### UI-SCR-06: Retire Document Confirmation Modal
- **Target Roles**: Client Admin
- **Phase**: Phase 2 (Task 2.8b)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Ingestion pipeline"
  - `docs/phases/PHASE-2.md`: Task 2.8
  - `docs/ui/KNOWLEDGE_PAGE_SPEC.md`: Section "Screens" item 2
- **What the User Sees and Does**:
  - Clicking "Retire" opens accessible modal dialog. Focus is trapped; Escape closes.
  - Prompts confirmation: "Are you sure you want to retire this document? Chunks will be deactivated from search immediately."
  - Confirming triggers `DELETE .../knowledge/documents/{document_id}` with CSRF token.
  - Document transitions to `Retired` state and action is greyed out.
- **Status**: **Built** (`web/components/ConfirmDialog.tsx`; verified by `knowledge_page.test.tsx` and UI-016 Pass).

#### UI-SCR-07: Flagged Pages Review Drawer
- **Target Roles**: Client Admin
- **Phase**: Phase 2 (Task 2.8b, Task 2.8a Step 2/3)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A client admin's flow" step 3), "Failure handling"
  - `docs/phases/PHASE-2.md`: Task 2.8
  - `docs/ui/KNOWLEDGE_PAGE_SPEC.md`: Section "Screens" item 3, "Review reason labels"
- **What the User Sees and Does**:
  - Clicking "Review" on a document in `Ready (N pages need review)` slides out a side drawer.
  - Lists each flagged page with: Page number, Reason badge (e.g. "Text unclear", "Image read failed", "Image read uncertain", "Image too large"), OCR confidence score, and extracted text snippet.
  - High-resolution rendered page image loaded via image proxy route. *(Note: route is currently client_admin RBAC only; chat will require staff access)*.
  - Per-page action buttons:
    - **Accept**: Clears flag, marks page verified (`POST .../pages/{page}/accept`).
    - **Exclude**: Removes page from search index (`POST .../pages/{page}/exclude`).
    - **Retry**: Re-enqueues page ingestion job (`POST .../pages/{page}/retry`). Handles 409 conflict gracefully ("This page is being processed. Try again in a moment.").
  - Version action: **Promote Version** button to activate clean version if all pages are resolved.
- **Status**: **Built** (`web/components/ReviewDrawer.tsx`; verified by `review_drawer.test.tsx`, `test_api_knowledge_step2.py / test_api_knowledge_step3.py`).

---

### Phase 3: Retrieval, Chat & Source Tracing

#### UI-SCR-08: Client Assistant Chat Interface (`/assistants/[assistantId]/chat`)
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.7)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A staff member's day"), "Build plan", "Model roles and runtime profiles" (`qwen3-vl:4b` for answer and vision roles on `desktop-gpu`)
  - `docs/phases/PHASE-3.md`: Task 3.7, Dependencies
- **What the User Sees and Does**:
  - Main messaging window with message thread, auto-scroll to latest response, and message input field with Send button (Enter submits).
  - Empty state with welcoming instructions ("Ask any clinical process, prior authorization, or payer question").
  - Sends question via `lib/api.ts` with CSRF token and credentials.
  - Real loading spinner while waiting for generation.
- **Status**: **Built** (`web/app/assistants/[assistantId]/chat/page.tsx`; verified by `test_graph_end_to_end.py`).

#### UI-SCR-09: Conversation History Sidebar
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.7)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A staff member's day" step 1: *"Conversations list on the left, chat in the middle."*)
  - `docs/phases/PHASE-3.md`: Task 3.7
- **What the User Sees and Does**:
  - Left-hand drawer or sidebar listing past conversations for the selected client assistant, sorted by recency.
  - Clicking a conversation restores message thread and active `conversation_id`.
  - "New Conversation" button to start a fresh un-checkpointed thread.
- **Status**: **Not built** (`conversations` DB table exists; API routes `GET .../conversations` and `GET .../messages` to be built when screen is connected).

#### UI-SCR-10: Client Assistant Selector / Switcher
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.7)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A staff member's day" step 1: *"Logs in and sees only the assistants for clients they belong to. Opens Client A Assistant."*)
  - `docs/phases/PHASE-3.md`: Task 3.7
- **What the User Sees and Does**:
  - Dropdown selector or modal dialog listing client assistants accessible to the authenticated user.
  - Switching assistants navigates to `/assistants/[newAssistantId]/chat` and resets conversation context.
- **Status**: **Not built** (Currently URL routes directly via `assistantId` or dev default; `GET /v1/assistants` route to be built when screen is connected).

#### UI-SCR-11: Streaming Pipeline Status Lines (Chat)
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.6, Task 3.7)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A staff member's day" steps 3–4: *"Sees 'Searching Client A documents...', then 'Page 31 is a scanned image, reading it now...', then the answer appears with a source chip."*)
  - `docs/phases/PHASE-3.md`: Task 3.6, Task 3.7
- **What the User Sees and Does**:
  - Dynamic progress message displayed inline above the incoming message bubble while LangGraph traverses nodes:
    - `"Searching {client_name} documents..."` (Node 3 `retrieve`)
    - `"Assessing evidence..."` (Node 5 `assess_evidence`)
    - `"Page {N} is a scanned image, reading it now..."` (Node 6 `vision_read` question-time status line for scanned pages; Spec section "End result: how people will use it")
    - `"Synthesizing answer..."` (Node 7 `generate`)
- **Status**: **Not built** (SSE streaming endpoint planned for Task 3.6).

#### UI-SCR-12: Grounded Answer Card with Interactive Citation Chips, Visual Badge & Language Label
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.7) & Phase 4 (Task 4.2)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Executive summary" Goal 4, "Security, HIPAA and guardrails" Invariant 5 (*"Visual readings are cited as visual evidence and displayed with page thumbnails"*), "End result: how people will use it" ("A staff member's day"), "Answer modes", "Language" (source language label for Spanish/translated docs)
  - `docs/phases/PHASE-3.md`: Task 3.7
  - `docs/phases/PHASE-4.md`: Task 4.2
- **What the User Sees and Does**:
  - Formatted answer card rendering verified text according to answer mode (`exact`, `short`, `explain`, `table`).
  - Source chips below answer text displaying Document Title, Version (`v1`), Page Number (`Page 12`), and Source Language label when applicable (e.g. `[Spanish]`; Spec section "Language").
  - Visual answer badge: lavender "Visual Reading" badge (`--color-lavender`) when `evidence_type === "visual"`.
  - Page thumbnail: rendered thumbnail image of the cited visual page alongside the answer (Goal 4, Invariant 5, Task 4.2).
  - Clicking a source chip triggers the embedded PDF.js source panel viewer.
- **Status**: **Partly built** (Answer card and source chips built in `chat/page.tsx:149-198`; page thumbnail and language label pending).

#### UI-SCR-13: Embedded PDF.js Source Panel Viewer
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.7)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A staff member's day" step 5: *"Clicks the source chip. A side panel opens the PDF at that page."*), "Tech stack"
  - `docs/phases/PHASE-3.md`: Task 3.7
- **What the User Sees and Does**:
  - Slide-out panel or split view embedding `pdf.js`.
  - Automatically jumps to the exact cited page (`#page=N`) with search term highlighting.
  - Allows staff to inspect original document context without downloading files locally.
- **Status**: **Not built** (PDF viewer component pending; tenant-scoped staff-accessible document file streaming route to be built when screen is connected).

#### UI-SCR-14: Refusal & Unanswerable Query Presentation
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.3, Task 3.7)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" ("A staff member's day": *"Asks something not in the documents. Gets: 'I don't have that in Client A's documents.'"*), "Failure handling"
  - `docs/phases/PHASE-3.md`: Task 3.3
- **What the User Sees and Does**:
  - Distinct neutral warning card when Node 5 (`assess_evidence`) determines evidence is weak or absent:
    `"I cannot find information about [topic] in [Client]'s validated documents."`
- **Status**: **UI renders it; backend path exists; not reachable from the UI on mock** (UI rendering code in `chat/page.tsx:202`; backend path in Node 5/7 verified by `test_retrieval_fallbacks.py:73` and `test_graph_end_to_end.py:150`; unreachable in live UI on mock because `questions.py:118` hardcodes mock evidence).

#### UI-SCR-15: Conflicting Evidence Resolution Presentation
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.3, Task 3.7)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Query flow" Node 5, "Failure handling" (*"Two documents disagree: with both citations"*)
  - `docs/phases/PHASE-3.md`: Task 3.3
- **What the User Sees and Does**:
  - Prominent "Conflicting Sources" badge with warning card when Node 5 (`assess_evidence`) detects contradictory evidence.
  - Renders both citations side-by-side (Document A, Version, Page vs Document B, Version, Page) so staff can immediately identify policy discrepancies.
- **Status**: **UI renders it; backend path exists; not reachable from the UI on mock** (UI rendering in `chat/page.tsx:225`; backend path verified by `test_graph_end_to_end.py:175`; unreachable in live UI on mock because `questions.py:118` hardcodes single non-conflicting evidence).

#### UI-SCR-16: Clarification Needed Presentation
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.7), Phase 1 (Task 1.6), ADR-0002
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Query flow" Node 2 `route` (*"clarify: 9 with the top 3 matching topics"*)
  - `docs/OPEN_QUESTIONS.md`: Item 1.6 (clarify topics via Retriever title search)
  - `docs/phases/PHASE-1.md`: Task 1.6
  - `docs/decisions/ADR-0002-clarify-topics-and-extraction-subgraph.md`: Section 1
- **What the User Sees and Does**:
  - When a query is ambiguous across departments, assistant responds with a clarification prompt and up to 3 clickable topic chips derived from stored document titles/headings via the Retriever.
  - Clicking a topic chip submits a clarified query.
- **Status**: **UI renders it; backend path exists; not reachable from the UI on mock** (UI renders Clarification Needed badge and text in `chat/page.tsx:302`; backend path verified by `test_retrieval_fallbacks.py:109`; clickable topic chips pending; unreachable in live UI on mock because `questions.py:118` does not route to clarify).

#### UI-SCR-17: General Task Draft Presentation (Ungrounded Clerical Draft)
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.7), Goal 6
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Executive summary" Goal 6 (general task fallback), "Query flow" Node 2 `route` (*"general: 7 (drafts response using general reasoning, marked as ungrounded)"*)
- **What the User Sees and Does**:
  - When a staff member asks for general administrative or clerical assistance not found in client documents (e.g. drafting a polite email template or formatting a schedule):
    Assistant drafts a helpful response using general reasoning, marked with a prominent ungrounded warning badge:
    `"General Task Draft — Not grounded in client knowledge documents"`.
- **Status**: **Not rendered by UI yet; backend path exists in graph** (Backend path passes in `backend/tests/integration/test_graph_end_to_end.py:83` `test_e2e_general_task`; chat frontend does not yet render specialized card).

#### UI-SCR-18: Safe Failure & System Error Presentation
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 3 (Task 3.7), Phase 1 (Task 1.6)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Query flow", "Observability and compliance logging"
  - `docs/FAILURE_MODES.md`: "Safe failure"
- **What the User Sees and Does**:
  - When backend encounters an unrecoverable failure, assistant card renders:
    `"An error occurred. Please contact support quoting reference ERR-XXXXXX."`
  - Internal request ID and stack traces are masked; random public error reference is displayed in monospace.
- **Status**: **UI renders it; backend path exists; not reachable from the UI on mock** (UI rendering in `chat/page.tsx:268`; backend path verified by `test_graph_end_to_end.py:195`; unreachable in live UI on mock during normal queries).

---

### Phase 4: Vision & PHI Field Extraction

#### UI-SCR-19: Chat Session Document Upload (Patient Fax / Intake Form)
- **Target Roles**: Staff
- **Phase**: Phase 4 (Task 4.3, Task 4.6)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" (Roles; Staff day step 6: *"Uploads a patient fax and asks for all fields."*)
  - `docs/phases/PHASE-4.md`: Task 4.3, Task 4.6
- **What the User Sees and Does**:
  - Upload icon / button inside chat input area allowing staff to upload patient files (PDF/image faxes).
  - Clear visual indicator: **"Session document — stays in this conversation only, never indexed into knowledge base"** (Invariant 7).
  - Progress bar while file is parsed and queued for extraction.
- **Status**: **Not built** (`session_documents` DB table exists; API route `POST /v1/conversations/{id}/documents` to be built when screen is connected).

#### UI-SCR-20: Side-by-Side Field Extraction Table & Page Preview
- **Target Roles**: Staff
- **Phase**: Phase 4 (Tasks 4.1 to 4.5, Task 4.6)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "End result: how people will use it" (Staff day step 6: *"Gets a field table beside the page image. Unreadable fields are blank and flagged, conflicting ones highlighted."*)
  - `docs/phases/PHASE-4.md`: Tasks 4.1 to 4.5 (`qwen3-vl:4b`, extraction subgraph `extract_fields` → `dual_read_compare` → `classify_confidence` → `validate_fields`, normalized visual comparison, field state engine), Task 4.6
- **What the User Sees and Does**:
  - Two-column split view in chat: uploaded document page image on the left, structured field table on the right.
  - Table rows display: Field Name (e.g. `Patient Name`, `DOB`, `NPI`, `Medication`), Extracted Value, Confidence Tier, and Validation State.
  - Interactive inspection: clicking a row highlights the corresponding bounding box / region on the page image.
- **Status**: **Not built**.

#### UI-SCR-21: Explicit Field State Badges (Extraction States)
- **Target Roles**: Staff
- **Phase**: Phase 4 (Task 4.5, Task 4.6)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Extraction and dual-read compare" (Field states for extraction), "Security, HIPAA and guardrails" Invariant 6
  - `docs/phases/PHASE-4.md`: Task 4.5 (field state engine)
- **What the User Sees and Does**:
  - Field status chips per row:
    - `FILLED_HIGH`: solid green (dual reads agree, check digits pass)
    - `FILLED_MEDIUM`: amber (single reading or lower confidence)
    - `CONFLICTING`: orange/red highlight showing both divergent values (e.g. "Read 1: 12345 | Read 2: 12346")
    - `UNREADABLE`: red/grey badge; value cell is blank with warning icon (never guessed)
    - `INVALID`: red warning (failed validation check digit or date format)
    - `NOT_PRESENT`: grey badge ("Not on document")
    - `NOT_APPLICABLE`: subdued badge ("N/A")
- **Status**: **Not built**.

---

### Phase 5: Hardening & Feedback

#### UI-SCR-22: Answer Feedback Controls (Thumbs Up / Down & Reason)
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 5 (Task 5.5)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Data model" table `feedback`
  - `docs/phases/PHASE-5.md`: Task 5.5
- **What the User Sees and Does**:
  - Subtle thumbs up / thumbs down icon buttons on every assistant answer card.
  - Clicking thumbs down opens small popover with structured reason options: "Inaccurate", "Too long", "Wrong source cited", "Other", with optional freeform text comment.
  - Submits feedback to backend `feedback` table for quality evaluation.
- **Status**: **Not built** (`feedback` DB table exists; API route `POST /v1/questions/{message_id}/feedback` to be built when screen is connected).

#### UI-SCR-23: Input Rate Limit & Guardrail Rejection State
- **Target Roles**: Staff, Client Admin
- **Phase**: Phase 5 (Task 5.1)
- **Source Doc & Section**:
  - `Client Knowledge Assistant Architecture and Build Plan.md`: "Query flow" Node 1 `guard_input`, "Security, HIPAA and guardrails"
  - `docs/phases/PHASE-5.md`: Task 5.1 (per-user and per-tenant rate limits on question endpoint; technology not specified in docs)
- **What the User Sees and Does**:
  - If a user or tenant exceeds question rate limits, input disables temporarily with countdown or message bubble renders:
    `"Query limit reached. Please wait X seconds before asking another question."` (HTTP 429).
- **Status**: **Partly built** (API rate limits exist on login; chat question query rate limiting UI not yet implemented).

---

## 3. Inventory Summary Statistics

| Phase | Total UI Screens & Panels | Built | Partly Built | UI Renders; Backend Exists; Not Reachable on Mock | Not Built |
|---|---|:---:|:---:|:---:|:---:|
| **Phase 1: Foundations & Auth** | 2 (`Login`, `TopBar`) | 2 | 0 | 0 | 0 |
| **Phase 2: Ingestion & Knowledge** | 5 (`Doc List`, `Upload Area`, `Status Chips`, `Retire Modal`, `Review Drawer`) | 5 | 0 | 0 | 0 |
| **Phase 3: Retrieval, Chat & Viewer** | 11 (`Chat Page`, `Conv History`, `Assistant Selector`, `Streaming Lines`, `Answer Card`, `PDF Viewer`, `Refusal Card`, `Conflict Card`, `Clarify Card`, `General Draft`, `Safe Failure`) | 1 | 1 | 4 | 5 |
| **Phase 4: Vision & Extraction** | 3 (`Session Upload`, `Side-by-Side Table`, `Field State Badges`) | 0 | 0 | 0 | 3 |
| **Phase 5: Hardening & Feedback** | 2 (`Feedback Controls`, `Rate Limit Warning`) | 0 | 1 | 0 | 1 |
| **Total** | **23** | **8** | **2** | **4** | **9** |

---

## 4. Architectural Boundaries & Decisions (Recorded with Srinath)

1. **Platform Admin Interface**:
   - The architecture specification describes Platform Admin operations (section "End result: how people will use it": *"Create clients and assistants, manage users, view traces and audit logs"*), while the fixed ordering rules mandate: *"No admin dashboards. The Knowledge page and its review list are the only admin screens"* (section "Build plan").
   - **Decision (Srinath)**: Platform admin has **no screen for now**; operations are executed out-of-band via CLI scripts (e.g. `scripts/seed_dev_data.py`) and direct migrations. This item remains documented as open for Dan's review (`docs/OPEN_QUESTIONS.md` Item 2.1) due to the specification tension between section "End result: how people will use it" and section "Build plan".
2. **Assistant Switcher Route**:
   - **Decision (Srinath)**: `GET /v1/assistants` (tenant-scoped discovery returning accessible assistants and display names) will be built when the assistant switcher screen is connected.
3. **Embedded PDF.js Document File Streaming**:
   - **Decision (Srinath)**: A dedicated, tenant-scoped, staff-allowed route (`GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/file`) will be built when the PDF viewer screen is connected to stream raw document bytes to `pdf.js`.
4. **Chat Conversation History & Session Document Upload**:
   - **Decision (Srinath)**: Conversation routes (`GET /v1/assistants/{assistant_id}/conversations`, `GET /v1/conversations/{conversation_id}/messages`) will be built in Phase 3 when the conversation history sidebar is connected. The session document upload route (`POST /v1/conversations/{conversation_id}/documents`) will be built in Phase 4 when session document upload is connected.
5. **Real-Time Streaming Protocol (SSE)**:
   - **Decision (Srinath)**: Streaming will receive a new endpoint in Task 3.6 (`POST /v1/questions/stream` using Server-Sent Events) and the synchronous JSON endpoint (`POST /v1/questions`) will remain intact, with an ADR authored at that time.
6. **Page Image Route RBAC**:
   - In code (`backend/app/api/routes/knowledge.py:290`), `get_page_image` requires `get_client_admin_tenant_context_for_assistant`. Because staff members in chat require page thumbnails (Invariant 5) and source previews (Spec section "End result: how people will use it"), a staff-allowed page image / document route scoped to the tenant is required for Phase 3 and Phase 4.

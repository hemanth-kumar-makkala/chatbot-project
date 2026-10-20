# Application Shell & Navigation Specification

## Overview
This document specifies the responsive Workspace and Assistant application shell for the Knowledge Assistant, superseding the legacy single-admin-screen paradigm per ADR-0014.

The interface is structured in two primary levels:
1. **Workspace Level**: Global assistant portfolio, tenant settings, and member controls.
2. **Assistant Level**: Dedicated per-assistant operational console containing Home (chat), Data sources, Instructions, Guardrails, Actions, Conversations, and Settings.

All unbuilt backend routes are explicitly labelled with a `"Not connected yet"` state in live mode (`NEXT_PUBLIC_DATA_SOURCE=live`), displaying a standardized grey badge and the required API endpoint.

---

## Screen Hierarchy & Route Map

| Screen | URL Path | Primary Purpose | Backend Route & Status |
| --- | --- | --- | --- |
| **Login** | `/login` | Staff authentication via cookie session | `POST /v1/auth/login` (Connected) |
| **Assistants Grid** | `/assistants` | View authorized assistants, open assistant console | `GET /v1/assistants` (Not built yet; fallback to `DEV_DEFAULT_ASSISTANT_ID` in live) |
| **New Assistant** | `/assistants` (Modal) | Provision new assistant in tenant workspace | `POST /v1/assistants` (Not built yet) |
| **Workspace Settings** | `/settings` | View workspace tenant name and member list | General: `GET /v1/auth/me` (Connected); Members: `GET /v1/tenant/members` (Not built yet) |
| **Assistant Home** | `/assistants/[id]` | Direct consultation chat with this assistant | `POST /v1/assistants/{id}/ask` (Connected) |
| **Data Sources** | `/assistants/[id]/data-sources` | Ingest and audit knowledge base files | Files: `GET/POST /v1/assistants/{id}/knowledge/*` (Connected); Snippet & Q&A (Not built yet) |
| **Instructions** | `/assistants/[id]/instructions` | Configure assistant tone and conversational scope | `GET/PATCH /v1/assistants/{id}/instructions` (Not built yet) |
| **Guardrails** | `/assistants/[id]/guardrails` | View locked safety rules, configure blocked topics | `GET/PATCH /v1/assistants/{id}/guardrails` (Not built yet) |
| **Actions** | `/assistants/[id]/actions` | External tool calling placeholder (disabled) | None (Planned for future release pending security review) |
| **Conversations** | `/assistants/[id]/conversations` | Historical conversation search and transcript audit | `GET /v1/assistants/{id}/conversations` (Not built yet) |
| **Assistant Settings** | `/assistants/[id]/settings` | Name, ID copy, danger zone (delete chats / assistant) | `PATCH/DELETE /v1/assistants/{id}` (Not built yet) |

---

## Navigation & App Shell Behavior

### 1. Left Sidebar
- **Header**:
  - Displays product brand: `APP_NAME` ("Knowledge Assistant").
  - Workspace selector: Displays current workspace tenant name (derived from `/v1/auth/me` tenant, fallback to `"Workspace"`).
  - Inside an assistant (`/assistants/[id]/*`): Displays current assistant name with an assistant switcher dropdown to quickly pivot between assistants or return to the `/assistants` directory.
- **Navigation Links (Assistant Scope)**:
  Sidebar links render in strict order:
  1. **Home** (`/assistants/[id]`)
  2. **Data sources** (`/assistants/[id]/data-sources`)
  3. **Instructions** (`/assistants/[id]/instructions`)
  4. **Guardrails** (`/assistants/[id]/guardrails`)
  5. **Actions** (`/assistants/[id]/actions`)
  6. **Conversations** (`/assistants/[id]/conversations`)
  7. **Settings** (`/assistants/[id]/settings`)
- **Footer**:
  - Current authenticated user email.
  - Workspace Settings link.
  - Logout action (`POST /v1/auth/logout`).

### 2. Responsive Breakpoints
- **Desktop (>= 768px)**: Persistent 260px left sidebar alongside flexible main content area.
- **Tablet / Mobile (< 768px)**:
  - Top app bar displays hamburger menu toggle button (`aria-label="Open navigation menu"`).
  - Sidebar collapses into an accessible slide-over drawer with overlay backdrop.
  - Full touch usability verified down to 375px viewport width.

---

## Screen Specifications

### 1. Workspace Assistants Grid (`/assistants`)
- **Header**: Workspace title, assistant count, and `"New assistant"` action button.
- **Cards Grid**:
  - Renders assistant cards with Name, Description, Last updated date, and Document count.
  - Card Action Menu (`...`):
    - `Open`: Navigates to `/assistants/[id]`.
    - `Settings`: Navigates to `/assistants/[id]/settings`.
    - `Delete`: Opens confirm dialog.
- **Live Mode Policy**:
  - The backend assistant collection endpoint (`GET /v1/assistants`) is not built yet.
  - In live mode (`NEXT_PUBLIC_DATA_SOURCE=live`), the UI renders the single known seeded assistant (`DEV_DEFAULT_ASSISTANT_ID` - Clinic A Assistant).
  - The `"New assistant"` modal displays a `"Not connected yet"` banner: `Provisioning assistants requires POST /v1/assistants (not built yet)`.
  - Card delete action displays `"Not connected yet"` banner: `Deleting assistants requires DELETE /v1/assistants/{id} (not built yet)`.

### 2. Workspace Settings (`/settings`)
- **Tabs**: `General` (Active), `Members` (Inactive/Placeholder).
- **General**: Displays Workspace Name and Tenant ID as read-only fields populated from `/v1/auth/me`.
- **Members**: Displays `"Not connected yet"`: `Member management requires GET /v1/tenant/members (not built yet)`.

### 3. Assistant Home (`/assistants/[id]`)
- Relocated core chat interface.
- **"New chat" Button**:
  - Always starts a completely empty conversation session.
  - Previous message turns, attachments, and answers are wiped from the active screen.
- **Chat Capabilities Preserved**:
  - Answer cards with interactive source chips (document, page, version).
  - Side panel PDF.js viewer with zoom and page controls.
  - Real page thumbnails.
  - Spec status lines during retrieval / vision execution.
  - Feedback controls (thumbs up / thumbs down with reason selector).
  - Attached document handling for staff session faxes (Invariant 7: never added to knowledge base).
  - All answer types (grounded text, table cite, visual reading, refusal, conflict, clarification, general draft, error fallback).

### 4. Data Sources (`/assistants/[id]/data-sources`)
- Restyled Knowledge management page.
- **Source Type Cards Row**:
  - `Add files`: Supports PDF, XLSX, CSV upload (Active, wired to `POST /v1/assistants/{id}/knowledge/upload`).
  - `Add text snippet`: Badge `"Coming later"`.
  - `Add Q&A`: Badge `"Coming later"`.
- **Document Management**:
  - Search filter input for document titles.
  - Documents status table with tokenized status chips.
  - Review Drawer for inspecting flagged pages, OCR confidence, and re-running failed extractions.
  - Document retirement modal with CSRF double-submit protection.
  - Total document size / page count summary line.

### 5. Instructions (`/assistants/[id]/instructions`)
- Dedicated prompt personalization interface.
- Textarea for defining assistant tone, audience, and operational scope.
- **Permanent Immutable Guardrail Notice**:
  > "Instructions shape tone and scope. Answers still come only from your documents."
  (Invariant 4: Model is strictly prevented from ignoring evidence or speaking outside client knowledge base).
- **Save Action**: Displays `"Not connected yet"` badge and note: `Updating instructions requires PATCH /v1/assistants/{id}/instructions (not built yet)`.

### 6. Guardrails (`/assistants/[id]/guardrails`)
- **Locked Built-in Rules (Read-Only & Permanently Active)**:
  1. *Answers only from documents or refuse* (Invariant 4)
  2. *Every answer cites document and page* (Invariant 5)
  3. *Never shows another client's data* (Tenant Isolation & RLS)
  4. *Unreadable fields are never guessed* (Invariant 6)
  5. *Documents uploaded in chat never enter the knowledge base* (Invariant 7)
- **Configurable Guardrails**:
  - Blocked topics input (list of custom topics for immediate refusal).
  - Custom refusal message input.
- **Save Action**: Displays `"Not connected yet"` badge and note: `Configuring guardrails requires PATCH /v1/assistants/{id}/guardrails (not built yet)`.

### 7. Actions (`/assistants/[id]/actions`)
- Dedicated placeholder for tool-calling capabilities.
- Notice: `"Coming soon. Actions that call other systems need a separate security review."`
- Explains that external tools touching patient health information (PHI) require isolated API credentials, audit logging, and explicit confirmation prompts before execution.

### 8. Conversations (`/assistants/[id]/conversations`)
- Transcript audit directory.
- Features:
  - Search input and Refresh button.
  - Conversation list panel displaying past sessions, timestamps, and question previews.
  - Selected conversation transcript panel displaying messages, source chips, and staff feedback marks.
  - `"New chat"` button navigating back to `/assistants/[id]` with a clean conversation state.
- **Live Mode Policy**:
  - Displays `"Not connected yet"` badge and note: `Conversation history requires GET /v1/assistants/{id}/conversations (not built yet)`.
  - Demo mode displays golden demo conversations.

### 9. Assistant Settings (`/assistants/[id]/settings`)
- **General Section**:
  - Assistant Name input.
  - Assistant ID field with one-click copy button.
  - Assistant Description textarea.
  - Save button: Displays `"Not connected yet"`: `Updating assistant details requires PATCH /v1/assistants/{id} (not built yet)`.
- **Danger Zone**:
  - `Delete all conversations`: Opens confirm dialog. Displays `"Not connected yet"`: `Deleting conversations requires DELETE /v1/assistants/{id}/conversations (not built yet)`.
  - `Delete assistant`: Opens confirm dialog. Displays `"Not connected yet"`: `Deleting an assistant requires DELETE /v1/assistants/{id} (not built yet)`.

---

## Backward Compatibility & Route Redirection
To ensure existing bookmarks, documentation, and external links continue functioning without 404 errors, legacy routes redirect as follows:
- `/assistants/[id]/chat` ➔ `/assistants/[id]` (Permanent client redirect to Home)
- `/assistants/[id]/knowledge` ➔ `/assistants/[id]/data-sources` (Permanent client redirect to Data sources)
- `/knowledge` ➔ `/assistants` (Redirect to Assistants directory)

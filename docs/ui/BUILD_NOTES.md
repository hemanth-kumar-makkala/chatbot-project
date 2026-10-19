# Knowledge Page Web App Build Notes

## 1. What Was Built Per Step

### Step 1: Spec Storage
- Saved exact UI specification to `docs/ui/KNOWLEDGE_PAGE_SPEC.md` defining internal staff knowledge page, brand tokens (WCAG AA), screens, review reasons, and accessibility rules.

### Step 2: Backend API Contract Audit
- Inspected backend route implementations in `backend/app/api/routes/` and `backend/app/api/app.py` and wrote `docs/ui/API_CONTRACT_NOTES.md` quoting exact files and line numbers.
- Documented auth routes (`/v1/auth/login`, `/v1/auth/logout`, `/v1/auth/me`), cookies (`session_token`, `csrf_token`), `X-CSRF-Token` header, all 9 knowledge endpoints under `/v1/assistants/{assistant_id}/knowledge`, error codes (400, 401, 403, 404, 409, 413, 422), port 8000, and CORS origin settings.
- Documented that no route exists to list assistants or fetch an assistant's name.

### Step 3: Web App Scaffolding
- Initialized `web/` with Next.js App Router, React 19, TypeScript strict mode (`"strict": true`), and plain CSS variables.
- Configured Vitest + React Testing Library + jsdom for testing.
- Created `next.config.mjs` with rewrite from `/v1/:path*` to `${API_ORIGIN}/v1/:path*`, enforcing non-empty `API_ORIGIN` at build/start.
- Created `web/.env.example`.
- Verified package version eligibility (all published $\ge 14$ days ago, on or before 2026-09-30) and pinned exact versions.
- Added scripts: `dev`, `build`, `start`, `typecheck` (`tsc --noEmit`), `test` (`vitest run`).
- Verified initial `npm install`, `npm run typecheck`, `npm test` (with smoke test), and `npm run build`.

### Step 4: Foundations & Authentication
- Built `web/styles/tokens.css` with all brand tokens as CSS variables (Staffingly Branding Guide: Primary blue `#0037B2`, Primary gold `#F5AF37`, Gradient `#0037B2` to `#A941C6`, Navy `#293682`, Purple `#5F3DBF`, Lavender `#D8C7FA`, Light blue `#C2DCFF`, Dark gold `#BD7800`, Cream `#FFF8F2`, Light lilac `#F8F2FF`, Barlow font stack).
- Built `web/styles/globals.css` with WCAG AA focus rings, reset, and responsive container (1280px max, 768px responsive).
- Built `web/lib/api.ts`: universal fetch wrapper with `credentials: "include"`, automatic `X-CSRF-Token` injection from cookie on `POST`, `PUT`, `PATCH`, `DELETE`, error masking to typed `{ ok: false, status, code }` that never leaks raw server text, and typed models matching the backend API contract.
- Built `web/lib/reviewReasons.ts` mapping all 7 review reason codes to human labels with fallback to "Needs review" and code-only console warning.
- Built `web/lib/statusChips.ts` mapping all document statuses to chips, including `Ready (N pages need review)`.
- Built `web/components/TopBar.tsx` with Staffingly wordmark, gradient background, assistant indicator, and user menu with logout.
- Built `web/app/login/page.tsx` with cookie-based session (never storing tokens in localStorage/sessionStorage) and accessible error display.
- Built unit tests in `web/__tests__/foundations.test.ts` (19 test cases) verifying CSRF header rules, error sanitization, review reason mapping, status chip mapping, and fallbacks.

### Step 5: Knowledge Page
- Built `web/components/StatusChip.tsx` with WCAG AA contrast colors per status.
- Built `web/components/ConfirmDialog.tsx` with focus trapping, Escape key closing, and accessible dialog markup.
- Built `web/components/UploadArea.tsx` with drag-and-drop or browse, 50 MB limit validation, extension and MIME type validation (`.pdf`, `.xlsx`, `.csv`), duplicate notice handling, and friendly error reporting.
- Built `web/components/DocumentsTable.tsx` rendering Title, Version, Status Chip, Uploaded Date, and Actions (Review, Retire with confirmation dialog).
- Built route `web/app/assistants/[assistantId]/knowledge/page.tsx` with polling every 5 seconds while any document is in non-final state, stopping polling when all documents are final (`ready`, `failed`, `retired`) and stopping polling when page is hidden via `visibilitychange`.
- Built unit tests in `web/__tests__/knowledge_page.test.tsx` (4 test cases) verifying document table rendering, polling lifecycle with fake timers, client-side upload validation (rejecting >50 MB and invalid types before sending), and retire confirmation dialog.

### Step 6: Review Drawer
- Built `web/components/ReviewDrawer.tsx`:
  - Flagged pages list displaying page number, reason label, and reading text.
  - Page image loading via `/v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/image`.
  - Actions per page: Accept (`acceptPage`), Exclude (`excludePage`), Retry (`retryPage`).
  - Version action: Promote Version (`promoteVersion`).
  - 409 conflict handling: displays fixed message `"This page is being processed. Try again in a moment."`.
  - Other error handling: displays fixed friendly message plus error code (e.g. `"Action failed (CODE). Please try again."`), never raw server text.
  - Keyboard accessibility: focus trap inside drawer, Escape key closes drawer, close button.
- Built unit tests in `web/__tests__/review_drawer.test.tsx` (8 test cases) verifying actions, 409 error presentation, code-only error presentation, and keyboard accessibility.

---

## 2. Exact Package Versions and Publish Dates

All packages are pinned to exact versions (no `^` or `~`) published at least 14 days prior to 2026-10-14 (on or before 2026-09-30):

| Package | Version | Publish Date (UTC) |
|---|---|---|
| `next` | `16.3.8` | `2026-09-30T16:07:21.198Z` |
| `react` | `19.3.0` | `2026-09-09T17:21:30.071Z` |
| `react-dom` | `19.3.0` | `2026-09-09T17:21:30.071Z` |
| `typescript` | `7.0.2` | `2026-07-08T15:55:18.431Z` |
| `@types/node` | `26.6.3` | `2026-09-25T22:06:16.779Z` |
| `@types/react` | `19.3.0` | `2026-09-09T18:08:49.750Z` |
| `@types/react-dom` | `19.3.0` | `2026-09-09T18:07:51.886Z` |
| `vitest` | `5.0.3` | `2026-09-30T11:30:42.429Z` |
| `@testing-library/react` | `16.3.3` | `2026-08-27T17:41:18.735Z` |
| `@testing-library/jest-dom` | `7.0.1` | `2026-08-09T23:44:33.598Z` |
| `jsdom` | `30.1.1` | `2026-09-22T02:07:00.270Z` |
| `@types/jsdom` | `30.0.0` | `2026-08-07T17:47:54.817Z` |
| `@vitejs/plugin-react` | `6.1.1` | `2026-08-28T03:30:56.619Z` |
| `vite` | `8.3.1` | `2026-09-24T12:26:19.940Z` |

---

## 3. How to Run Locally (Mock Profile Demo)

### 1. Seed Development Data
Provision synthetic tenants, users, and assistants:
```powershell
cd backend
uv run python scripts/seed_dev_data.py
```
- Seeded Client Admin: `admin@clinic-a.local` / `DevPassword123!`
- Seeded Assistant ID: `00000000-0000-0000-0002-000000000001`

### 2. Start Backend API
Run FastAPI application via the `app.api.app:create_app` factory with `APP_PROFILE=mock` and `COOKIE_SECURE=false` (for local HTTP):
```powershell
cd backend
$env:APP_PROFILE="mock"
$env:COOKIE_SECURE="false"
uv run python -c "import uvicorn; from app.api.app import create_app; from app.api.config import ApiSettings; uvicorn.run(create_app(ApiSettings(profile='mock', storage_dir='./data/files', cookie_secure=False)), host='127.0.0.1', port=8000)"
```

### 3. Start Ingestion Worker
Run Procrastinate background worker for document splitting, page processing, and periodic sweeps:
```powershell
cd backend
$env:APP_PROFILE="mock"
uv run procrastinate --app=app.worker.app worker --queue=ingestion --queue=builtin
```

### 4. Start Frontend Web App
Start Next.js dev server forwarding `/v1/*` to the backend:
```powershell
cd web
$env:API_ORIGIN="http://localhost:8000"
npm run dev
```
Navigate to:
- Login: `http://localhost:3000/login` (log in with `admin@clinic-a.local` / `DevPassword123!`)
- Knowledge Page: `http://localhost:3000/assistants/00000000-0000-0000-0002-000000000001/knowledge`

### 5. Run Web Tests and Validation
```powershell
cd web
npm run typecheck
npm test
$env:API_ORIGIN="http://localhost:8000"; npm run build
```

---

## 4. Backend Gaps Found

1. **No Route to List Assistants or Retrieve Assistant Name**:
   - The backend exposes `/v1/assistants/{assistant_id}/knowledge/...` routes requiring an existing `assistant_id` in the URL path.
   - There is no endpoint (such as `GET /v1/assistants` or `GET /v1/assistants/{id}`) to list accessible assistants or fetch an assistant's display name.
   - UI Accommodation: The top bar renders the `assistantId` from the route params if no client name is available.
2. **Binary Request Body for Upload vs Standard Multipart**:
   - The backend `upload_document` route reads binary bytes directly from `request.stream()` with header `X-Filename` (or query param `?filename=`), rather than standard `multipart/form-data`.
   - UI Accommodation: `lib/api.ts` `uploadDocument` sends the `File` object directly as the request body with `X-Filename: file.name` and matching MIME `Content-Type`.
3. **User Profile Scope**:
   - `/v1/auth/me` returns `UserResponse` with `id`, `email`, `name`, `tenant_id`. It does not return assistant assignments or client roles.

---

## 5. Items From the Spec That Could Not Be Done and Why

- None. All requirements from `docs/ui/KNOWLEDGE_PAGE_SPEC.md` were implemented:
  - Cookie-based authentication without storing tokens in localStorage or sessionStorage.
  - Automatic `X-CSRF-Token` header attached from cookie on `POST`, `PUT`, `PATCH`, `DELETE`.
  - Brand tokens defined as CSS variables with WCAG AA compliance.
  - Drag-and-drop upload with 50 MB limit validation and PDF/XLSX/CSV enforcement before sending.
  - Document table with all status chips (`Queued`, `Reading`, `Scanning images`, `Ready`, `Ready (N pages need review)`, `Failed`, `Retired`).
  - 5-second polling that stops when all documents are in final states and pauses on page visibility hidden.
  - Retire confirmation dialog with focus trap and Escape handler.
  - Review drawer displaying flagged pages, page images, reading text, per-page Accept/Exclude/Retry actions, Promote Version action, 409 job-active message, and error sanitization hiding raw server text.

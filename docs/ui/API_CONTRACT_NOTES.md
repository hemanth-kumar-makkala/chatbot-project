# Backend API Contract Notes (v1)

Source of truth for frontend integration with the backend API. Quoting files and line numbers from `backend/app/`.

---

## a. Authentication & Session Routes

Defined in `backend/app/api/routes/auth.py`.

### 1. Login
- **Method & Path**: `POST /v1/auth/login` (`backend/app/api/routes/auth.py:46`)
- **Request Body**: JSON `LoginRequest` (`backend/app/api/routes/auth.py:30-33`)
  ```json
  {
    "email": "user@example.com",
    "password": "string"  // pragma: allowlist secret
  }
  ```
- **Response Shape**: 200 OK `LoginResponse` (`backend/app/api/routes/auth.py:35-38, 109`)
  ```json
  {
    "email": "user@example.com",
    "status": "authenticated"
  }
  ```
  *Security Note*: Never returns tokens in JSON body.
- **Cookies Set** (`backend/app/api/routes/auth.py:88-107`):
  - Session Cookie: Name `session_token` (from `ApiSettings.cookie_name`, `backend/app/api/config.py:41`), `httpOnly=True`, `secure=True` (or configured `COOKIE_SECURE`), `samesite="lax"`, `path="/"`.
  - CSRF Cookie: Name `csrf_token` (from `ApiSettings.csrf_cookie_name`, `backend/app/api/config.py:42`), `httpOnly=False`, `secure=True`, `samesite="lax"`, `path="/"`. Readable by browser script to populate the `X-CSRF-Token` header.
- **Error Codes**:
  - `401 Unauthorized`: `"Invalid email or password"` (`backend/app/api/routes/auth.py:76-79`).
  - `429 Too Many Requests`: `"Too many failed login attempts. Please wait before retrying."` (`backend/app/api/routes/auth.py:63-66`).

### 2. Logout
- **Method & Path**: `POST /v1/auth/logout` (`backend/app/api/routes/auth.py:112`)
- **Headers**: `X-CSRF-Token` required on state-changing requests (`backend/app/api/routes/auth.py:118`, `backend/app/api/deps.py:68-78`).
- **Request Body**: None (`backend/app/api/routes/auth.py:113-119`).
- **Response Shape**: 200 OK (`backend/app/api/routes/auth.py:128`)
  ```json
  {
    "detail": "Logged out successfully"
  }
  ```
- **Cookies Cleared**: `session_token` and `csrf_token` are deleted (`backend/app/api/routes/auth.py:125-126`).
- **Error Codes**:
  - `403 Forbidden`: `"CSRF token verification failed"` (`backend/app/api/deps.py:75-78`).

### 3. Current Session / User (`/me`)
- **Method & Path**: `GET /v1/auth/me` (`backend/app/api/routes/auth.py:131`)
- **Request Body**: None.
- **Response Shape**: 200 OK `UserInfoResponse` (`backend/app/api/routes/auth.py:40-44, 146-150`)
  ```json
  {
    "user_id": "00000000-0000-0000-0000-000000000000",
    "email": "user@example.com",
    "is_platform_admin": false
  }
  ```
- **Error Codes**:
  - `401 Unauthorized`: `"Authentication required"` or `"Invalid or expired session"` (`backend/app/api/deps.py:95-98, 104-107`).
  - `404 Not Found`: `"User not found"` (`backend/app/api/routes/auth.py:142-145`).

### CSRF Protection Details
- **CSRF Header Name**: `X-CSRF-Token` (`backend/app/api/deps.py:72`, `backend/app/api/app.py:49`).
- **CSRF Cookie Name**: `csrf_token` (`backend/app/api/config.py:42`, `backend/app/api/deps.py:73`).
- **Enforcement**: Required on all `POST`, `PUT`, `DELETE`, `PATCH` requests when authenticated with browser session cookie (`backend/app/api/deps.py:68-78`).

---

## b. Routes under `/v1/assistants/{assistant_id}/knowledge`

Defined in `backend/app/api/routes/knowledge.py:48`.
All routes require client admin permissions via `get_client_admin_tenant_context_for_assistant` (`backend/app/api/deps.py:171-225`). If the assistant does not exist or the user lacks client admin membership, the backend returns `404 Not Found` for anti-probing defense (`backend/app/api/deps.py:124, 137, 144`).

### 1. Upload Document
- **Method & Path**: `POST /v1/assistants/{assistant_id}/knowledge/upload` (`backend/app/api/routes/knowledge.py:104-109`)
- **Headers**:
  - `X-CSRF-Token`: CSRF token from cookie (`backend/app/api/routes/knowledge.py:108`)
  - `X-Filename`: Filename (e.g. `guide.pdf`) (`backend/app/api/routes/knowledge.py:134-136`)
- **Query Parameters**:
  - `filename`: Alternative to `X-Filename` (`backend/app/api/routes/knowledge.py:135`)
  - `document_id`: (Optional) Target document UUID for replacement version (`backend/app/api/routes/knowledge.py:140`)
- **Request Body**: Raw binary stream of file bytes (streamed in-memory up to 50 MB cap; not multipart form data) (`backend/app/api/routes/knowledge.py:124-133`).
- **Response Shape**: 202 Accepted `UploadResponse` (`backend/app/api/routes/knowledge.py:51-61, 228-233`)
  ```json
  {
    "document_id": "00000000-0000-0000-0000-000000000000",
    "document_version_id": "00000000-0000-0000-0000-000000000000",
    "status": "queued",
    "is_duplicate": false
  }
  ```
  *(If duplicate content is detected: `"status": "duplicate"`, `"is_duplicate": true`)*.
- **Error Codes**:
  - `400 Bad Request`: Validation failure (empty file, unsupported file extension, corrupt PDF structure, etc.) (`backend/app/api/routes/knowledge.py:203-207`).
  - `403 Forbidden`: Client admin role required or CSRF check failed (`backend/app/api/routes/knowledge.py:193-197`, `backend/app/api/deps.py:75-78`).
  - `404 Not Found`: Assistant not found, or target `document_id` not found in tenant (`backend/app/api/routes/knowledge.py:145-148, 158-161, 198-202`).
  - `413 Content Too Large`: `"File size exceeds upload limit"` (> 50 MB) (`backend/app/api/routes/knowledge.py:129-132`).
  - `422 Unprocessable Entity`: Malware detected (`backend/app/api/routes/knowledge.py:208-212`).

### 2. List Documents
- **Method & Path**: `GET /v1/assistants/{assistant_id}/knowledge/documents` (`backend/app/api/routes/knowledge.py:236-239`)
- **Request Body**: None.
- **Response Shape**: 200 OK (`backend/app/api/routes/knowledge.py:248-249`)
  ```json
  {
    "documents": [
      {
        "document_id": "00000000-0000-0000-0000-000000000001",
        "title": "Clinical Protocols",
        "active_version_id": "00000000-0000-0000-0000-000000000002",
        "created_at": "2026-10-14T12:00:00Z",
        "is_retired": false,
        "versions": [
          {
            "version_id": "00000000-0000-0000-0000-000000000002",
            "ui_status": "Ready",
            "raw_status": "ready",
            "flagged_pages_count": 0,
            "total_pages_count": 10,
            "created_at": "2026-10-14T12:00:00Z"
          }
        ]
      }
    ]
  }
  ```
  - **Derived `ui_status` values** (`backend/app/adapters/db/postgres_document_repository.py:970-1000`):
    - `"Queued"`
    - `"Reading"`
    - `"Scanning images"`
    - `"Ready"`
    - `"Ready (N page need review)"` / `"Ready (N pages need review)"`
    - `"Failed"`
    - `"Retired"`
- **Error Codes**:
  - `401 Unauthorized` (`backend/app/api/deps.py:95, 105`).
  - `404 Not Found` (`backend/app/api/deps.py:124, 137, 144`).

### 3. Get Version Review Details
- **Method & Path**: `GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/review` (`backend/app/api/routes/knowledge.py:251-255`)
- **Request Body**: None.
- **Response Shape**: 200 OK `VersionReviewResponse` (`backend/app/api/routes/knowledge.py:63-68, 280-283`)
  ```json
  {
    "version_id": "00000000-0000-0000-0000-000000000002",
    "flagged_pages": [
      {
        "page_id": "00000000-0000-0000-0000-000000000010",
        "page_number": 3,
        "review_reason": "low_ocr_confidence",
        "ocr_confidence": 0.58,
        "page_type": "scanned",
        "is_excluded": false,
        "has_image": true,
        "readings": [
          {
            "id": "00000000-0000-0000-0000-000000000020",
            "source": "ocr",
            "text": "Partial text extracted...",
            "model_id": "docling_easyocr",
            "model_version": "1.0",
            "created_at": "2026-10-14T12:05:00Z"
          }
        ]
      }
    ]
  }
  ```
- **Error Codes**:
  - `401 Unauthorized` (`backend/app/api/deps.py:95, 105`).
  - `404 Not Found`: Document version not found or assistant not found (`backend/app/api/routes/knowledge.py:265-268`, `backend/app/api/deps.py:124`).

### 4. Get Page Image
- **Method & Path**: `GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/image` (`backend/app/api/routes/knowledge.py:286-292`)
- **Request Body**: None.
- **Response Shape**: 200 OK Raw PNG byte stream (`media_type="image/png"`, `Cache-Control="no-store"`, `X-Content-Type-Options="nosniff"`, `Content-Security-Policy="default-src 'none'"`) (`backend/app/api/routes/knowledge.py:340-348`).
- **Error Codes**:
  - `401 Unauthorized` (`backend/app/api/deps.py:95, 105`).
  - `404 Not Found`: Page or page image not found (`backend/app/api/routes/knowledge.py:311-314, 325-328`).

### 5. Retire Document
- **Method & Path**: `DELETE /v1/assistants/{assistant_id}/knowledge/documents/{document_id}` (`backend/app/api/routes/knowledge.py:351-359`)
- **Headers**: `X-CSRF-Token` required (`backend/app/api/routes/knowledge.py:354`).
- **Request Body**: None.
- **Response Shape**: 200 OK `RetireDocumentResponse` (`backend/app/api/routes/knowledge.py:70-76, 385-389`)
  ```json
  {
    "document_id": "00000000-0000-0000-0000-000000000001",
    "is_retired": true,
    "status": "retired"
  }
  ```
- **Error Codes**:
  - `401 Unauthorized` (`backend/app/api/deps.py:95, 105`).
  - `403 Forbidden`: CSRF verification failed (`backend/app/api/deps.py:75-78`).
  - `404 Not Found`: Document not found (`backend/app/api/routes/knowledge.py:370-373`).

### 6. Accept Page Review
- **Method & Path**: `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/accept` (`backend/app/api/routes/knowledge.py:392-401`)
- **Headers**: `X-CSRF-Token` required (`backend/app/api/routes/knowledge.py:395`).
- **Request Body**: None.
- **Response Shape**: 200 OK `PageActionResponse` (`backend/app/api/routes/knowledge.py:78-85, 445-450`)
  ```json
  {
    "version_id": "00000000-0000-0000-0000-000000000002",
    "page_number": 3,
    "status": "accepted",
    "auto_promoted": true
  }
  ```
- **Error Codes**:
  - `401 Unauthorized` (`backend/app/api/deps.py:95, 105`).
  - `403 Forbidden`: CSRF check failed (`backend/app/api/deps.py:75-78`).
  - `404 Not Found`: Document, version, or page not found (`backend/app/api/routes/knowledge.py:410-413`).
  - `409 Conflict`:
    - `"Document is retired"` (`backend/app/api/routes/knowledge.py:417`)
    - `"PAGE_JOB_ACTIVE"`: Job is actively processing (`backend/app/api/routes/knowledge.py:427`)
    - `"INVALID_PAGE_STATE"`: Page cannot be accepted in current state (`backend/app/api/routes/knowledge.py:422`)
  - `422 Unprocessable Content`: `"NO_USABLE_READING"` (`backend/app/api/routes/knowledge.py:432`).

### 7. Exclude Page Review
- **Method & Path**: `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/exclude` (`backend/app/api/routes/knowledge.py:453-462`)
- **Headers**: `X-CSRF-Token` required (`backend/app/api/routes/knowledge.py:456`).
- **Request Body**: None.
- **Response Shape**: 200 OK `PageActionResponse` (`backend/app/api/routes/knowledge.py:78-85, 501-506`)
  ```json
  {
    "version_id": "00000000-0000-0000-0000-000000000002",
    "page_number": 3,
    "status": "excluded",
    "auto_promoted": false
  }
  ```
- **Error Codes**:
  - `401 Unauthorized` (`backend/app/api/deps.py:95, 105`).
  - `403 Forbidden`: CSRF check failed (`backend/app/api/deps.py:75-78`).
  - `404 Not Found`: Document, version, or page not found (`backend/app/api/routes/knowledge.py:471-474`).
  - `409 Conflict`:
    - `"Document is retired"` (`backend/app/api/routes/knowledge.py:478`)
    - `"PAGE_JOB_ACTIVE"`: Page currently processing (`backend/app/api/routes/knowledge.py:488`)
    - `"INVALID_PAGE_STATE"`: Page cannot be excluded in current state (`backend/app/api/routes/knowledge.py:483`)

### 8. Promote Version
- **Method & Path**: `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/promote` (`backend/app/api/routes/knowledge.py:509-517`)
- **Headers**: `X-CSRF-Token` required (`backend/app/api/routes/knowledge.py:512`).
- **Request Body**: None.
- **Response Shape**: 200 OK `VersionPromoteResponse` (`backend/app/api/routes/knowledge.py:87-93, 549-553`)
  ```json
  {
    "version_id": "00000000-0000-0000-0000-000000000002",
    "status": "ready",
    "is_active": true
  }
  ```
- **Error Codes**:
  - `401 Unauthorized` (`backend/app/api/deps.py:95, 105`).
  - `403 Forbidden`: CSRF check failed (`backend/app/api/deps.py:75-78`).
  - `404 Not Found`: Document or version not found (`backend/app/api/routes/knowledge.py:524-527`).
  - `409 Conflict`:
    - `"Document is retired"` (`backend/app/api/routes/knowledge.py:530`)
    - `"INVALID_VERSION_STATE"`: Cannot promote version in current state (`backend/app/api/routes/knowledge.py:536`)

### 9. Retry Page Processing
- **Method & Path**: `POST /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/pages/{page_number}/retry` (`backend/app/api/routes/knowledge.py:556-565`)
- **Headers**: `X-CSRF-Token` required (`backend/app/api/routes/knowledge.py:559`).
- **Request Body**: None.
- **Response Shape**: 200 OK `PageRetryResponse` (`backend/app/api/routes/knowledge.py:95-102, 628-633`)
  ```json
  {
    "version_id": "00000000-0000-0000-0000-000000000002",
    "page_number": 3,
    "needs_review": true,
    "status": "retrying"
  }
  ```
- **Error Codes**:
  - `401 Unauthorized` (`backend/app/api/deps.py:95, 105`).
  - `403 Forbidden`: CSRF check failed (`backend/app/api/deps.py:75-78`).
  - `404 Not Found`: Document, version, or page not found (`backend/app/api/routes/knowledge.py:573-576`).
  - `409 Conflict`:
    - `"DOCUMENT_RETIRED: Document is retired"` (`backend/app/api/routes/knowledge.py:580`)
    - `"PAGE_JOB_ACTIVE"`: Job already active (`backend/app/api/routes/knowledge.py:590`)
    - `"INVALID_PAGE_STATE"`: Cannot retry page in current state (`backend/app/api/routes/knowledge.py:585`)
    - `"RETRY_NOT_USEFUL"`: Retry not useful (`backend/app/api/routes/knowledge.py:595`)
  - `500 Internal Server Error`: Background job dispatch failure (`backend/app/api/routes/knowledge.py:624-626`).

---

## c. Assistant Listing / Assistant Name Routes

- **Status**: **NO route exists** in the backend API to list assistants that a user can access, nor to fetch an assistant's metadata/name.
- **Findings**:
  - Grep across all routes (`backend/app/api/routes/`) reveals `assistant_id` is only accepted as a URL path parameter (`/v1/assistants/{assistant_id}/knowledge/...` and `/v1/questions`).
  - The backend resolves the assistant ID to an internal tenant UUID using an internal SQL helper `lookup_assistant_tenant(asst_id)` (`backend/app/api/deps.py:132`).
  - `GET /v1/auth/me` returns `user_id`, `email`, and `is_platform_admin`, but zero assistant references (`backend/app/api/routes/auth.py:40-44`).
- **UI Treatment**:
  - Top bar displays "Staffingly" wordmark and user email / logout.
  - Assistant name is derived from the route path parameter `assistantId` as fallback (e.g. `Assistant: ${assistantId}`), satisfying the spec instruction: *"assistant (client) name if the API provides it"*.

---

## d. CORS Settings & Backend Port

- **Backend Port**: `8000` (`docker-compose.yml:32, 38, 73`).
- **CORS Allowed Origins**: `ApiSettings.cors_origins` defaults to `["http://localhost:3000"]` (`backend/app/api/config.py:37-40`). Wildcard `"*"` is strictly forbidden (`backend/app/api/app.py:22-23`).
- **CORS Allowed Methods**: `["GET", "POST", "PUT", "DELETE", "OPTIONS"]` (`backend/app/api/app.py:48`).
- **CORS Allowed Headers**: `["Content-Type", "Authorization", "X-CSRF-Token", "X-Request-ID"]` (`backend/app/api/app.py:49`).
- **CORS Allow Credentials**: `True` (`backend/app/api/app.py:47`).
- **Next.js Rewrite**: In `next.config`, all `/v1/*` calls from the browser to Next.js (port 3000) are rewritten to `API_ORIGIN` (e.g. `http://localhost:8000/v1/*`), keeping cookies strictly same-origin.

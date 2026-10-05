# ADR-0007: Session Authentication, Cookie Hardening, and CSRF Protection

## Status
Accepted (Phase 1, Task 1.8)

## Context
The Client Knowledge Assistant requires staff and administrative authentication to enforce tenant boundaries before any document retrieval or question processing begins (Invariant 2 and Invariant 3). Because users interact with the backend via web browsers (Next.js client in Phase 3), the authentication layer must protect session credentials against theft via Cross-Site Scripting (XSS), prevent unauthorized cross-origin requests via Cross-Site Request Forgery (CSRF), and prevent tenant or assistant ID probing.

## Decision

### 1. Hashed Opaque Session Tokens
- Authenticators issue high-entropy opaque random strings (`secrets.token_urlsafe(32)`).
- Session tokens are **never** stored in plaintext in the database; only their SHA-256 hex digests are persisted in the global `sessions` table.
- Passwords are verified using timing-safe `Argon2id` password hashing.

### 2. Idle Timeout and Absolute Expiry
- Sessions enforce dual expiry gates:
  - **Absolute Expiry**: Default 24 hours from creation.
  - **Idle Timeout**: Default 30 minutes from `last_seen_at`.
- Revocation (`revoked_at`) marks the session immediately invalid upon logout.

### 3. HttpOnly Session Cookies (Zero Body Disclosures)
- Successful login writes the session token exclusively to an `httpOnly`, `Secure`, `SameSite=Lax` cookie (`session_token`).
- The plaintext token is **never** returned in the JSON response body. This prevents token exfiltration via client-side JavaScript (mitigating XSS credential theft).
- Bearer tokens in headers are disabled by default in standard configuration and permitted strictly for automated testing harnesses.

### 4. CSRF Protection via Double-Submit Token Pattern
- For state-changing requests (POST, PUT, DELETE, PATCH) using cookie authentication:
  - The login endpoint issues a cryptographically random `csrf_token` cookie (accessible by the frontend client).
  - The frontend client must read this cookie and supply its value in the `X-CSRF-Token` HTTP header.
  - The API dependency validates that the header matches the cookie before processing the request. State-changing requests lacking matching tokens are rejected with HTTP 403 Forbidden.

### 5. CORS Allowlist
- CORS middleware permits only explicitly configured web origins from application settings (`CORS_ORIGINS`).
- Wildcard origin (`"*"`) is strictly prohibited and asserted against at application factory initialization.

### 6. Anti-Probing Tenant Routing (404 Over 403)
- Question requests specify an `assistant_id`.
- The assistant's `tenant_id` is resolved via database function `lookup_assistant_tenant`.
- User membership in that tenant is verified for every caller (platform admins have no tenant access without explicit membership).
- If the assistant does not exist OR if the user lacks a membership in that tenant, the endpoint returns **HTTP 404 Not Found**, never 403 Forbidden, preventing attackers from probing valid assistant IDs.
- Caller-supplied `tenant_id` in headers, query, or body is ignored.

### 7. Append-Only `auth_events` Log
- Authentication attempts (success and failure) write to a global, append-only `auth_events` table (user_id if known, outcome, reason code, client IP hash, timestamp).
- The `app_user` database role has `INSERT` privileges only (no `UPDATE` or `DELETE`).
- Tenant-scoped compliance auditing remains in `audit_events`.

## Consequences
- Session credentials are safe from client-side script inspection.
- CSRF attacks on state-changing endpoints are prevented.
- Cross-tenant data probing via assistant IDs is blocked.
- Pre-commit security and AST import boundaries remain fully compliant.

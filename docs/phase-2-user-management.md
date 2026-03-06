# Phase 2 — User Management Module (1.a)

> **Goal:** Implement the complete user lifecycle — sign-up, email verification, login, token refresh, logout, profile management, and avatar upload.

---

## Scope

| Feature ID | Feature | Source Doc |
|------------|---------|------------|
| 1.a.a | User Sign-up | `docs-2/user-sign‑up.md` |
| 1.a.b | User Login | `docs-2/user-login.md` |
| 1.a.c | User Profile Management | `docs-2/user-profile-management.md` |
| — | Module Overview | `docs-2/user-management.md` |

---

## Deliverables

### 2.1 User Service (NestJS)
Create the `user-service` microservice with the following handlers:

#### Authentication Endpoints
| Method | Path | Handler |
|--------|------|---------|
| `POST` | `/api/v1/auth/signup` | Create user (Argon2id hash), create profile (trigger or transaction), generate email verification token (SHA-256), send email, emit `UserCreated` Kafka event, audit log |
| `GET` | `/api/v1/auth/verify?token=` | Validate SHA-256 hashed token, activate user, audit log |
| `POST` | `/api/v1/auth/login` | Validate credentials (Argon2id), issue JWT (RS256, 15 min) + refresh token (bcrypt hash, 7 days), rate-limit (10/min/tenant-email), audit log |
| `POST` | `/api/v1/auth/refresh` | Verify bcrypt-hashed refresh token, issue new JWT, audit log |
| `POST` | `/api/v1/auth/logout` | Delete refresh token row, audit log |

#### Profile Endpoints
| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/v1/users/me` | Return user + profile joined data; enforce `status = active` |
| `PATCH` | `/api/v1/users/me` | Partial update (firstName, lastName, locale); optimistic concurrency via `If-Modified-Since`; audit log |
| `POST` | `/api/v1/users/me/avatar` | Validate MIME (PNG/JPEG) + size (≤ 5 MB); upload to S3; generate signed URL; update `profiles.avatar_url`; audit log |

### 2.2 Database Migrations
- Migration `006-create-profile-trigger.sql` (idempotent) — auto-create `profiles` row on user insert.
- Verify all indexes: `idx_users_email`, `idx_users_tenant_id`, `idx_email_token`, `idx_refresh_user`.

### 2.3 Email Service Integration
- SMTP / SendGrid integration for verification emails.
- Template for verification link: `https://app.example.com/verify?token={plainToken}`.

### 2.4 Kafka Events
- `UserCreated` event (Avro schema) published on successful sign-up.
- Consumer group for downstream services (analytics, audit).

### 2.5 Security Implementation
- Argon2id password hashing (memory ≥ 64 MiB, parallelism = 2).
- Bcrypt (cost = 12) for refresh token hashing.
- SHA-256 for email verification token hashing.
- Redis rate-limiting: sign-up (5/hr), login (10/min), avatar (5/min).
- Feature flags: `signup_enabled`, `login_enabled`, `profile_management_enabled`.

### 2.6 Frontend — Auth Pages (React/Next.js)
- Sign-up page (email, password, tenant ID)
- Login page
- Email verification landing page
- Profile view/edit page with avatar upload
- All pages use MUI component library

---

## Acceptance Criteria
- [ ] User can sign up, receive verification email, verify, and log in
- [ ] JWT contains correct claims (`sub`, `tenantId`, `role`, `exp`)
- [ ] Refresh token flow works end-to-end
- [ ] Profile view, update, and avatar upload work correctly
- [ ] Rate limiting enforced on all auth endpoints
- [ ] Audit logs created for every auth event
- [ ] RLS prevents cross-tenant data access
- [ ] All unit, integration, and contract tests pass in CI
- [ ] E2E Cypress test for full user journey

---

## Dependencies
- **Phase 1:** Kubernetes, PostgreSQL, Redis, Keycloak, API Gateway, CI/CD

## Risk Mitigation
| Risk | Mitigation |
|------|------------|
| Email service outage | Feature flag `email_enabled` can disable; resend later |
| Argon2id library compatibility | Pin version in package.json; test in CI |
| Brute-force attacks | Redis rate-limiting + account lockout after 5 failures |

---

*Estimated Duration: 2–3 weeks*

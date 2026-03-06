# Phase 5 — Admin Panel Module (1.d)

> **Goal:** Deliver the tenant administration layer — user CRUD, usage analytics, and system settings — completing the platform.

---

## Scope

| Feature ID | Feature | Source Doc |
|------------|---------|------------|
| 1.d.a | User Management (Admin) | `docs-2/user-management.md` (admin section) |
| 1.d.b | Usage Analytics | `docs-2/usage-analytics.md` |
| 1.d.c | System Settings | `docs-2/system-settings.md` |
| — | Module Overview | `docs-2/admin-panel.md` |

---

## Deliverables

### 5.1 Admin Service (NestJS)

#### User Management Endpoints (Admin)
| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/v1/admin/users` | Paginated user list (tenant-scoped); returns id, email, role, status, created_at |
| `GET` | `/api/v1/admin/users/{userId}` | Single user detail with profile data |
| `PATCH` | `/api/v1/admin/users/{userId}` | Update role (owner/admin/member/viewer) or status (active/suspended); audit log |
| `DELETE` | `/api/v1/admin/users/{userId}` | Soft-delete: set status=suspended, clear PII from profiles; retain audit_logs; return 204 |

**RBAC Rules:**
- Only `owner` or `admin` can access `/admin/*`
- `owner` can modify anyone in tenant
- `admin` can modify users with `role ≠ owner`
- Cannot demote the last `owner` (returns 409 CONFLICT)

#### Usage Analytics Endpoints
| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/v1/admin/usage?start=&end=` | Daily aggregates from `usage_metrics` (messages_sent, tokens_used); includes summary (totals, averages); paginated |
| `GET` | `/api/v1/admin/usage/export?start=&end=` | CSV export (streamed, `Content-Type: text/csv`) |
| `GET` | `/api/v1/admin/usage/plan-limit` | Returns current plan quotas (messages, tokens) |

#### System Settings Endpoints
| Method | Path | Handler |
|--------|------|---------|
| `GET` | `/api/v1/admin/settings` | Read tenant's plan, data_retention config, and feature_flags from `system_settings` |
| `PATCH` | `/api/v1/admin/settings` | Update plan (free/pro/enterprise), data_retention (days), and/or feature_flags (JSON merge patch); emit `SystemSettingsChanged` Kafka event AND publish immediate Redis invalidation; audit log with before/after snapshots |

### 5.2 Feature Flag Management
- All flags stored in `system_settings.feature_flags` (JSONB column)
- PATCH endpoint performs a deep merge (only overrides supplied keys)
- `SystemSettingsChanged` Kafka event notifies downstream services (billing, chat, search) For eventual consistency tasks.
- **Immediate Cache Invalidation:** To prevent UI/API Gateway consistency races, the admin service synchronously fires a Redis Pub/Sub message (`invalidate_tenant:TENANT_ID`). All downstream services process this to flush their local LRU caches instantly, ensuring the new feature flags apply to the very next request via ETag / Config-Version headers.

### 5.3 GDPR Compliance ("Right to be Forgotten")
- `DELETE /admin/users/{id}` workflow:
  1. Set `users.status = suspended`
  2. Clear `profiles.first_name`, `last_name`, `avatar_url`
  3. Retain `audit_logs` and `refresh_tokens` for compliance
  4. Delete S3 avatar object
- Conversation data: Admin can call `DELETE /api/v1/conversations/{id}` to remove message history

### 5.4 Security
- JWT RBAC: `owner` or `admin` required for all admin endpoints
- Redis rate-limiting: 20 admin requests/min per tenant
- PostgreSQL RLS enforces tenant isolation at DB level
- Audit log for every mutating action (before/after payloads in JSONB)

### 5.5 Frontend — Admin Dashboard (React/Next.js)

| Page | Components |
|------|------------|
| **User List** | Paginated table, role/status badges, edit/delete actions |
| **User Edit** | Role dropdown, status toggle, confirmation dialog |
| **Usage Analytics** | Date range picker, daily table, summary cards, line chart (messages + tokens over time) |
| **CSV Export** | Download button, progress indicator |
| **System Settings** | Plan selector (free/pro/enterprise), feature flag toggles, save button |

All pages use MUI components with the shared design system.

---

## Acceptance Criteria
- [ ] Admin can list, view, edit roles, and suspend/reactivate users
- [ ] Cannot demote the last owner (409 error)
- [ ] Soft-delete clears PII from profiles
- [ ] Usage analytics shows correct daily aggregates for a selected date range
- [ ] CSV export downloads correctly formatted data
- [ ] Plan and feature flags can be updated via settings page
- [ ] `SystemSettingsChanged` event emitted on settings update
- [ ] All admin actions create audit log entries
- [ ] Rate limiting enforced (20 req/min)
- [ ] RLS prevents cross-tenant data access
- [ ] E2E test: admin login → user management → analytics → settings

---

## Dependencies
- **Phase 1:** Kubernetes, PostgreSQL, Redis, API Gateway
- **Phase 2:** User authentication and profile data
- **Phase 3:** Document data (for GDPR deletion of associated docs)
- **Phase 4:** Chat Engine populates `usage_metrics` and `conversations/messages`

## Risk Mitigation
| Risk | Mitigation |
|------|------------|
| Last owner removal | Business rule check with 409 response |
| CSV export of large datasets | Streaming response to avoid OOM |
| Feature flag misconfiguration | Whitelist validation; before/after snapshot in audit log |
| Admin privilege escalation | RBAC enforced at gateway + service layer + RLS |

---

*Estimated Duration: 2–3 weeks*

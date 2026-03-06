# Phase 1 — Foundation & Infrastructure

> **Goal:** Stand up the core infrastructure, database schema, authentication layer, and CI/CD pipeline that every other phase depends on.

---

## Scope

| Module | Features Included |
|--------|-------------------|
| **Infrastructure** | Kubernetes cluster, Terraform, Helm charts, Docker registry |
| **Data Layer** | PostgreSQL (managed), Redis, S3 / MinIO |
| **Auth** | Keycloak (OAuth2/OIDC), JWT issuance (RS256) |
| **API Gateway** | Kong / Envoy with TLS 1.3, rate limiting, JWT validation |
| **CI/CD** | GitHub Actions pipeline (lint → test → build → deploy) |
| **Observability** | Prometheus, Grafana, OpenTelemetry, ELK stack |

---

## Deliverables

### 1.1 Infrastructure-as-Code
- Terraform modules for Kubernetes cluster (EKS/GKE/AKS), managed PostgreSQL, ElastiCache (Redis), S3/Glacier.
- Helm chart templates for each service with parameterized values per environment (`dev`, `staging`, `prod`).
- Namespace isolation: one Kubernetes namespace per environment.
- Redis pre-configured for Pub/Sub (for immediate cache invalidation).

### 1.2 Database Schema (PostgreSQL)
Run all baseline migrations:

| Migration | Script | Creates |
|-----------|--------|---------|
| 001 | `001-create-users.sql` | `users` table (UUID PK, email, password_hash, tenant_id, status, role, timestamps) |
| 002 | `002-create-profiles.sql` | `profiles` table (user_id FK, first_name, last_name, avatar_url, locale) |
| 003 | `003-create-refresh-tokens.sql` | `refresh_tokens` table |
| 004 | `004-create-audit-logs.sql` | `audit_logs` table (immutable append-only) |
| 005 | `005-create-email-verifications.sql` | `email_verifications` table |
| 006 | `006-create-profile-trigger.sql` | `trg_user_insert` trigger |
| 007 | `007-create-system-settings.sql` | `system_settings` table (tenant_id PK, plan, feature_flags JSON) |

**Database Partitioning:** Native PostgreSQL table partitioning horizontally chunks append-heavy tables (`audit_logs`) by `RANGE (created_at)`, creating a new partition per month automatically to prevent query bloat.

**Row-Level Security (RLS):** Every table with `tenant_id` gets an RLS policy:
```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

### 1.3 Authentication Service
- Deploy Keycloak with realm configuration for the SaaS platform.
- Configure OAuth2/OIDC flows.
- RSA-256 JWT signing keys stored in HashiCorp Vault.
- Token TTLs: access = 15 min, refresh = 7 days.

### 1.4 API Gateway
- Kong / Envoy deployment with:
  - TLS 1.3 termination
  - JWT validation plugin
  - Rate-limiting plugin (Redis-backed token bucket)
  - CORS configuration
  - Route definitions for `/api/v1/auth/*`, `/api/v1/users/*`, `/api/v1/documents/*`, `/api/v1/search/*`, `/api/v1/chat/*`, `/api/v1/admin/*`

### 1.5 CI/CD Pipeline (GitHub Actions)
```yaml
# Stages: lint → unit test → build Docker → push → deploy (staging) → smoke test → promote (prod)
```
- Automated DB migration as a pre-deploy hook.
- Helm-based deployments with rolling updates.
- Docker image tagging with git SHA.

### 1.6 Observability Stack
- Prometheus scraping `/metrics` on all services.
- Grafana dashboards for latency, error rates, pod health.
- ELK stack for structured JSON logs.
- OpenTelemetry SDK integrated into the NestJS service template.

---

## Acceptance Criteria
- [ ] Kubernetes cluster running with 3 namespaces (dev, staging, prod)
- [ ] PostgreSQL migrations 001–007 applied without errors
- [ ] RLS policies active on all tenant-scoped tables
- [ ] Keycloak issuing valid JWTs with `sub`, `tenantId`, `role`, `exp`
- [ ] API Gateway routing requests, validating JWTs, enforcing rate limits
- [ ] CI/CD pipeline successfully builds, tests, and deploys a "hello world" NestJS service
- [ ] Prometheus/Grafana dashboards showing metrics from deployed services

---

## Dependencies
None — this is the foundational phase.

## Risk Mitigation
| Risk | Mitigation |
|------|------------|
| Cloud provider outage | Multi-AZ deployment for PostgreSQL; S3 cross-region replication |
| Secrets leak | All credentials in Vault; never in env vars or config files |
| Migration failure | Idempotent scripts; disposable test DB in CI |

---

*Estimated Duration: 2–3 weeks*

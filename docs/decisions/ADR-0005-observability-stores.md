# ADR-0005: Observability Stores, Operational Allowlist, and Resilience Topology

## Status
Accepted

## Context
The architecture specification (*Client Knowledge Assistant: Architecture and Build Plan*, Section "Logging and observability", lines 429–452) mandates three separate stores tied together by `request_id`:
1. **Operational logs**: JSON to file locally, Cloud Logging in GCP. Metadata and counts only.
2. **Trace store (`run_steps`)**: PostgreSQL, per-node step execution, token counts, and error references.
3. **Audit log (`audit_events`)**: PostgreSQL, append-only compliance record of actions and actors.

A critical design requirement is determining the failure behavior and privacy guarantees of each store:
- How to strictly prevent PHI and raw query text from leaking into operational logs.
- Whether a logging or telemetry failure should abort an active user request.
- How auditing must behave when compliance records cannot be guaranteed.

## Decisions

### 1. Operational Logger: Strict Field Allowlist and No Free-Form Text
- Operational logging uses a hard allowlist: `ts`, `request_id`, `tenant_id`, `node`, `status`, `latency_ms`, `counts`, `route`, `profile`, `model_id`, `error_code`, `error_type`.
- Any unallowlisted field (e.g., `question_body`, `prompt_content`, `document_text`) is rejected, dropped from the JSON record, and counted in a telemetry metric (`get_dropped_field_count()`).
- No free-form message field (`message` or format string) is emitted into output JSON.
- Exceptions record strictly `error_type` and `error_code`. Raw exception messages and stack traces are excluded from operational logs because exception strings frequently contain patient data, database query snippets, or file paths (Presidio exception scrubbing arrives in Phase 5 as a second line of defense).
- `request_id` and `tenant_id` propagate automatically across async coroutine boundaries via Python `contextvars`.

### 2. Trace Store (`run_steps`): Independent Transactions and Fail-Open Resilience
- `PostgresTracer` persists execution steps (`step_start`, `step_end`, `step_error`) in its own dedicated transaction, separate from the application request's transaction.
- If an application request encounters an error or rolls back its database transaction, the telemetry rows in `run_steps` remain committed, providing complete visibility into which layer failed without needing raw query logs.
- All tracer database writes are bounded by a strict write timeout (`write_timeout_seconds`, default 1.0s).
- **Tracer is Fail-Open**: If the tracer database write times out or fails (e.g., database connection outage), the tracer logs an operational warning with an error code and returns safely without raising. Telemetry collection must never disrupt user availability or fail a question request (Invariant and ADR-0004 contract).

### 3. Audit Store (`audit_events`): Fail-Closed Compliance Enforcement
- `AuditLogger` records immutable compliance actions (who viewed, uploaded, exported, or deleted what, and when).
- **Audit is Fail-Closed**: In sharp contrast to telemetry, compliance auditing is a regulatory invariant. If writing an audit event fails for any reason (e.g., database unavailable, constraint violation), `AuditLogger` raises an `AuditError`. The calling application operation MUST abort and refuse the action. An unrecorded administrative or compliance-sensitive action is never permitted to succeed.

## Comparison Summary

| Store | Scope | Privacy | Transaction | Failure Behavior |
| --- | --- | --- | --- | --- |
| **Operational Logs** | Node timing, status, counts | Zero-PHI by design (strict allowlist) | N/A (JSON stream) | Fail-open (standard logging) |
| **Trace Store (`run_steps`)** | Execution steps, tokens, error refs | Internal IDs & metadata only | Dedicated independent transaction | **Fail-open** (timeout & silent fallback) |
| **Audit Log (`audit_events`)** | Actors, actions, and resource refs | References only, append-only | Dedicated tenant transaction | **Fail-closed** (raises `AuditError`) |

## Consequences
- Compliance guarantee: audited operations cannot occur without a persisted record.
- Availability guarantee: temporary database spikes or tracing slowdowns do not delay or abort user questions.
- Privacy guarantee: operational logs cannot accidentally leak PHI via developer logging statements or exception strings.

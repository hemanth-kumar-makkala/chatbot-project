# Open Questions & Architecture Decisions

This document tracks open architecture questions, closed items resolved by the updated specification and user architectural guidance, and phase-deferred decisions.

---

## 1. Closed Decisions & Clarifications

### 1.1 Diagram Placeholders Resolved
- **Status**: **CLOSED**
- **Resolution**: The four embedded content placeholders were converted into authoritative text and tables in the updated specification:
  - **7 Layers**: Section "Proposed architecture" (lines 164–173).
  - **9 Query Flow Nodes**: Section "Query flow" (lines 191–205).
  - **8 Ingestion Steps**: Section "Ingestion pipeline" (lines 240–252).
  - **Phase Roadmap & Exit Gates**: Section "Build plan" table (lines 573–583).

### 1.2 Embedding Model Consistency in Cloud
- **Status**: **CLOSED**
- **Resolution**: Section "Tech stack" table (line 334) confirms that GCP staging and production use the exact same embedding model (`BGE-M3`, 1024-dim), self-hosted in cloud. This avoids re-embedding all documents or managing mismatched vector dimensions.

### 1.3 Presidio Memory Footprint on 8 GB Dev PC
- **Status**: **CLOSED**
- **Resolution**: Section "Build plan" roadmap table (line 580) explicitly places Presidio exception scrubbing into Phase 5 ("Hardening"). Phase 1 on the 8 GB office PC (mock profile, Docker capped at 3 GB) does not load Presidio models.

### 1.4 Dual Vision Reading Comparison Risk
- **Status**: **CLOSED**
- **Resolution**: Section "Query flow" under "Implementation notes" (line 209) resolves fuzzy visual matching: "Visual comparison uses normalized values. Two vision readings never match word for word, so compare the extracted values after normalizing them (digits, dates, IDs, amounts). Prose may differ; an ID may not."

### 1.5 LangGraph Checkpoint vs State Payload Risk
- **Status**: **CLOSED**
- **Resolution**: Section "Query flow" under "Implementation notes" (line 208) resolves the state design: "Request-scoped context. Question text and evidence text are loaded once per request into a context object that lives outside the checkpointed graph state. Checkpoints hold IDs only."

### 1.6 Clarify Path Topic Generation in Node 2 (`route`)
- **Status**: **CLOSED**
- **Resolution**: In Node 2, the top 3 matching topics for clarification are obtained from a lightweight, read-only search of document titles and section headings strictly for that tenant, performed through the `Retriever` port. Topics never originate from model generation, enforcing Invariant 4 and the fundamental architectural rule that the system answers only from evidence.

### 1.7 Extraction Flow Topology in LangGraph
- **Status**: **CLOSED**
- **Resolution**: Extraction is engineered as a separate subgraph (`extract_fields` → `dual_read_compare` → `classify_confidence` → `validate_fields`), invoked when Node 2 (`route`) routes to extraction. The extraction subgraph reuses Node 1 (`guard_input`) and Node 9 (`respond`). This keeps the primary 9-node knowledge query flow clean and enables graph contract tests to validate each graph independently.

### 1.8 Tracer Execution Mode and Logging Topology
- **Status**: **CLOSED**
- **Resolution**: `Tracer` methods (`step_start`, `step_end`, `step_error`) are defined as asynchronous coroutines to persist execution step records (`run_steps`) to PostgreSQL. Operational logs (metadata only, no content) use standard Python logging outside `ports/`. By contract invariant, a tracing failure must never fail the request (fail-safe contract recorded in ADR-0004).

### 1.9 UI Architecture, Administrative Boundaries & Endpoint Strategy (Task UI-0)
- **Status**: **CLOSED (DECIDED BY SRINATH)**
- **Resolution**:
  - **Platform Admin UI**: Platform admin has **no screen for now**; client creation, assistant provisioning, and user assignment run out-of-band via CLI scripts (`scripts/seed_dev_data.py`) and migrations. Formal dashboard resolution remains open for Dan due to the spec conflict between line 78 and line 588.
  - **Assistant Discovery**: Endpoint `GET /v1/assistants` (returning tenant-scoped accessible assistants and display names) will be built when the assistant switcher screen is connected.
  - **PDF File Streaming Route**: A dedicated, tenant-scoped, staff-allowed route `GET /v1/assistants/{assistant_id}/knowledge/documents/{version_id}/file` will be built when the PDF viewer screen is connected to stream original document bytes to `pdf.js`.
  - **Conversation History & Session Upload Routes**: `GET /v1/assistants/{assistant_id}/conversations` and `GET /v1/conversations/{conversation_id}/messages` will be built in Phase 3 when the conversation history sidebar is connected. `POST /v1/conversations/{conversation_id}/documents` will be built in Phase 4 when session document upload is connected.
  - **Streaming Protocol (SSE)**: Streaming will receive a new endpoint in Task 3.6 (`POST /v1/questions/stream` using Server-Sent Events) and the synchronous JSON endpoint (`POST /v1/questions`) will remain intact, with an ADR recorded at that time.

---

## 2. Active Open Decisions (Spec Lines 653–662)

The following items from the spec's "Open decisions" remain open and require stakeholder agreement:

- [ ] **Retention Periods** (Spec line 655): Retention periods for messages, session uploads (`session_documents`), checkpoints, and traces (pending Dan and compliance).
- [ ] **Pilot Workload Sizing** (Spec line 656): Pilot workload definition: users, questions per day, documents and pages per month (feeds Cloud SQL sizing and the <$100/mo cloud budget).
- [ ] **Pilot Client and Document Sample** (Spec line 657): Formal selection of the first pilot client and acquisition of an approved de-identified document sample.
- [ ] **Cloud Spend Caps** (Spec line 658): Explicit monetary spend cap per tenant in cloud with alerting thresholds.
- [ ] **Office LAN Model Server** (Spec line 659): Confirmation whether the office network permits using the desktop GPU PC as a model server for the 8 GB office PC (IT check).
- [ ] **Production Auth Provider** (Spec line 660): Authentication in production: built-in hashed sessions vs Identity Platform (checking BAA coverage).
- [ ] **Extraction Schemas** (Spec line 661): Formal JSON schemas for the initial set of PHI document types (intake faxes, prior auth forms, clinical records).
- [ ] **Spreadsheet Merged Cell Propagation** (Task 2.6): In `openpyxl` `read_only=True` mode, merged cell blocks contain data only in the top-left cell while remaining cells in the span evaluate to `None`. Ingestion preserves this verbatim in `tables_data`. To be revisited with real client documents if tabular QA requires forward-filling merged headers or values.

### 2.1 Platform Admin Cross-Tenant Query Topology & UI Boundary (Architecture Design)
- **Status**: **DOCUMENTED / OPEN FOR DAN**
- **Context**: In Task 1.4a, `memberships.role` is restricted strictly to `client_admin` and `staff` via database check constraint. Platform administration is represented as `users.is_platform_admin` (boolean). All tenant table queries strictly require a `TenantContext` and execute `SET LOCAL app.current_tenant_id`, guaranteeing total RLS isolation.
- **Tension in Spec**: Architecture line 78 states *"Platform admin: Create clients and assistants, manage users, view traces and audit logs"*, while line 588 decrees *"No admin dashboards. The Knowledge page and its review list are the only admin screens"*.
- **Decision (Srinath)**: Platform admin has **no screen for now**; administrative tasks run via out-of-band CLI scripts. Left open for Dan to determine whether a scoped Platform Admin screen is required for production.
- **Question**: When platform administrators require operational telemetry or administrative reports across clients, what is the approved topology?
  - *Option A (Tenant Switching)*: Platform admin explicitly assumes a specific client's tenant context; cross-tenant queries across multiple tenants simultaneously are disallowed.
  - *Option B (Dedicated Admin Query Service)*: Out-of-band operational reporting reads de-identified run metrics using an unscoped reporting role.
  - *Current Implementation*: Option A enforced — all application database queries against tenant tables require a valid `TenantContext`. Cross-tenant queries are blocked by RLS policies.

### 2.2 Production Malware Scanner Provider Selection (Task 2.2)
- **Status**: **OPEN FOR STAKEHOLDER REVIEW**
- **Context**: Task 2.2 defines the `MalwareScanner` port interface and introduces an allowlist guard restricting `MockMalwareScanner` strictly to local synthetic profiles (`mock`, `office-cpu`, `desktop-gpu`, `home-gpu`, `dev-free-api`). Wiring refuses to start on `cloud`, `frontier`, or unknown profiles.
- **Question**: Which production malware scanning engine/service should be adopted for cloud deployments?
  - *Option A (ClamAV Container/Daemon)*: Open-source, local daemon or sidecar container on Cloud Run / Compute, zero per-scan API cost, compliant under BAA.
  - *Option B (Google Cloud Web Risk / VirusTotal API)*: Managed API, high fidelity, requires verifying HIPAA BAA coverage and recurring API costs under the <$100/mo cloud budget.
### 2.3 Storage Orphan File Garbage Collection & Sweeper Pipeline (Task 2.2)
- **Status**: **OPEN FOR STAKEHOLDER REVIEW**
- **Context**: Document upload writes raw objects to storage before committing document and version metadata to PostgreSQL. In standard execution, application-level error handlers and lost concurrency races delete uncommitted files immediately via `storage.delete(key)`. However, an abrupt server crash, OOM kill, or network partition between storage write and database commit could leave an orphan file in storage.
- **Question**: What reconciliation sweeper topology should be implemented to garbage collect unreferenced storage objects?
  - *Option A (Nightly Reconciliation Worker)*: A scheduled maintenance worker iterates tenant storage prefixes, checks for corresponding rows in `document_versions` / `pages`, and prunes unreferenced files older than 24 hours.
  - *Option B (Two-Stage Staging Bucket)*: Write uploads to a staging directory with automated short TTL retention (e.g. 24h), copying/promoting only upon DB transaction commit.
  - *Option C (Deferred to Phase 5 Hardening)*: Implement sweeper as part of the Phase 5 infrastructure audit and maintenance scripts.

### 2.4 Vision Confidence Calibration & Threshold (Phase 2 Model Adapter)
- **Status**: **PROVISIONAL / CALIBRATION REQUIRED**
- **Context**: Vision confidence uses the geometric mean token probability from Ollama logprobs. It is uncalibrated: it measures model certainty, not correctness. The 0.70 threshold is provisional and must be calibrated on the golden set VISUAL_RECOVERY documents before Phase 3 baseline metrics.
- Observed on a synthetic fax: a misread drug name (OCREVUS read as OCREEVUS) still scored 0.79, above the 0.70 threshold. Calibration must check that misreads of names, IDs and numbers fall below the threshold.

---

## 3. Phase-Deferred Architecture Decisions

### 3.1 BGE-M3 Self-Hosting Topology on GCP (Phase 6 Decision)
- **Status**: **DEFERRED TO PHASE 6**
- **Context**: BGE-M3 sits entirely behind the `EmbeddingProvider` port interface; core engine code does not depend on its hosting topology.
- **Decision Driver**: While a separate internal Cloud Run service is standard, scale-to-zero introduces cold-start latency to initial queries. The decision between a dedicated internal Cloud Run service versus co-location inside the API/worker container will be decided in Phase 6 based on measured cold-start latency and monthly cost profiling.

### 3.2 In-Memory Login Rate Limiter Migration to Shared Store (Phase 6 Decision)
- **Status**: **DEFERRED TO PHASE 6**
- **Context**: In Task 1.8, failed login attempts are rate-limited via an in-memory sliding window limiter (`LoginRateLimiter`) on the local development server to protect against brute-force attacks.
- **Decision Driver**: In-memory rate limiting operates correctly for single-process local servers on the 8 GB office PC. Before multi-instance cloud deployment on Cloud Run in Phase 6, rate limiting state must be migrated to a shared distributed store (e.g., Redis/Valkey on Memorystore) or delegated to an edge gateway/Cloud Armor rule to enforce rate limits across horizontally scaled instances.

### 3.3 Per-Account Lockout vs Per-IP Rate Limiting (Phase 5 Decision - Hardening)
- **Status**: **DEFERRED TO PHASE 5 (HARDENING)**
- **Context**: Task 1.8 implements per-IP sliding-window rate limiting (`LoginRateLimiter`) with HMAC client IP pseudonymization and Argon2 dummy timing attack mitigation.
- **Decision Driver**: Per-account lockout protects against distributed credential stuffing attacks targeting specific high-privilege accounts from rotating IP pools. However, unmitigated per-account lockout can expose the application to denial-of-service (DoS) attacks against legitimate staff members. In Phase 5 ("Hardening"), a balanced per-account lockout mechanism (progressive backoff, CAPTCHA, or admin unlock workflows) will be specified alongside the Phase 5 security audit and Presidio exception scrubbing.

### 3.4 CPU vs CUDA PyTorch for Linux Cloud Workers (Phase 6 Decision)
- **Status**: **DEFERRED TO PHASE 6**
- **Context**: Document parsing (Docling, EasyOCR) and vector embeddings (BGE-M3) utilize PyTorch. On the desktop GPU PC, CUDA acceleration is used. On GCP cloud deployments, workers could run either standard CPU wheels or GPU-accelerated workloads (e.g. Cloud Run with Nvidia L4 or GKE).
- **Decision Driver**: CPU PyTorch wheels are ~200 MB, start quickly, and scale down to zero on Cloud Run at low compute cost within the <$100/mo cloud budget, but inference is slower. GPU instances provide sub-second OCR/embedding latency but incur higher idle and hourly costs. The choice between CPU-only vs CUDA PyTorch for Linux cloud workers will be evaluated and decided in Phase 6 based on batch ingestion volume and cloud spend telemetry.

### 3.5 Multilingual Support
- **Status**: **DEFERRED**
- Multilingual (Spanish and others) is deferred. Existing Spanish code, the EasyOCR Spanish model and test_real_docling_ocr_spanish_scanned_page stay as they are. No new multilingual work until the core application is complete. Golden set documents A11, A12 and E03 are deferred.

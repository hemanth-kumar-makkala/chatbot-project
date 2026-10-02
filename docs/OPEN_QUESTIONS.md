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

---

## 3. Phase-Deferred Architecture Decisions

### 3.1 BGE-M3 Self-Hosting Topology on GCP (Phase 6 Decision)
- **Status**: **DEFERRED TO PHASE 6**
- **Context**: BGE-M3 sits entirely behind the `EmbeddingProvider` port interface; core engine code does not depend on its hosting topology.
- **Decision Driver**: While a separate internal Cloud Run service is standard, scale-to-zero introduces cold-start latency to initial queries. The decision between a dedicated internal Cloud Run service versus co-location inside the API/worker container will be decided in Phase 6 based on measured cold-start latency and monthly cost profiling.

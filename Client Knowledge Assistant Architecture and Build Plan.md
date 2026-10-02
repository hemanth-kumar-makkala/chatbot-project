# Client Knowledge Assistant: Architecture and Build Plan

## Summary

We are building a staff-facing, per-client knowledge assistant to replace Chatbase, built on our own engine, run locally at $0, and deployed later on GCP under our existing BAA.

**The philosophy in one sentence:** the system retrieves evidence, decides whether that evidence is trustworthy, uses vision only when a page needs it, and lets the AI model speak only from validated evidence inside the logged-in user's client scope.

**The engine is fixed. Everything around it is swappable.** Models, storage, OCR tools and hosting are plug-in parts behind interfaces. Today we run free open-source models on an office PC with no GPU. Tomorrow we can plug in Gemini, Claude or GPT API keys by changing configuration, not code.

| Item | Status |
| --- | --- |
| Architecture | Agreed in principle, this document |
| Budget, local build | $0 |
| Budget, cloud pilot | Target under $100/month for a defined pilot workload (to be measured) |
| Hosting target | GCP, under the existing Google Cloud BAA |
| Builder | Antigravity (Gemini), working from this spec and its tests |
| First product | Staff chatbot per client, with PHI document field extraction |

## Problem statement

Our client chatbots run on Chatbase, a third-party tool, and it fails on exactly the content our clients have: PDFs with images inside, scanned faxes, tables and mixed languages.

What goes wrong today:

- **Image content is invisible.** Text inside images or scanned pages is not reliably read, so we have worked around it by hosting images on a separate portal and linking to them.
- **Answers drift from the source.** The model rewrites stored information instead of returning it as written. Prompt tweaks did not fix this, because the problem sits in retrieval and answer handling, not in the prompt.
- **Answer length does not match the question.** A one-line question gets a paragraph.
- **No control over data handling.** Healthcare content and PHI pass through a third party, and we cannot inspect retrieval, logs or failures.
- **Cost and lock-in.** We pay for a platform we cannot extend, and we cannot choose the models.

The hard technical problem is not the model. It is **finding the right page, reading it correctly even when it is an image, and answering only from what is actually there.**

## Goals, non-goals and success criteria

The first release succeeds when staff get correct, sourced, short answers from their client's documents, including image pages, and the system refuses instead of guessing.

**Goals**

1. Answer client and process questions from that client's knowledge base only.
2. Read image and scanned pages, both at upload and when a question needs them.
3. Return stored information as written, at the length the question needs.
4. Show the source: document, version, page, and a page thumbnail.
5. Extract fields from PHI documents staff upload, leaving unreadable fields blank and flagged.
6. Handle general tasks (drafting an email) without the knowledge base, without stating client facts.
7. Support many users and many clients at once, each isolated from the others.
8. Run the same engine on an office PC, a GPU PC, and GCP, with only configuration changing.

**Non-goals for release 1**

- Video or audio files in the knowledge base.
- Writing into EHRs or other client systems.
- Patient-facing chat.
- ColPali-style visual embeddings or a separate vector database (revisit only if measurements show OCR plus vision reads are failing).
- Very large archives (the 20 GB scenario). The design must not block it, but we do not build for it now.

**Success criteria (targets set after the first measured baseline)**

| Measure | Why it matters |
| --- | --- |
| Retrieval Hit@5 | The right page is found |
| Citation precision and recall | The cited page actually supports the answer |
| Faithfulness | Every claim is in the evidence |
| Refusal accuracy | Says "I don't know" exactly when it should |
| Extraction false-fill rate | A field that should be blank was filled. The most important number |
| Cross-tenant leak count | Must be zero, always |
| p95 latency, text path and vision path | Measured separately |
| Cost per question and per page ingested | Feeds the cloud budget |

## End result: how people will use it

To a staff member it feels like a messaging app: pick the client's assistant, ask, get a short answer with its source. All knowledge is uploaded from the interface, never from the backend.

**Roles**

| Role | Can do |
| --- | --- |
| Platform admin (Staffingly) | Create clients and assistants, manage users, view traces and audit logs |
| Client admin | Upload, replace and delete that client's knowledge files, see processing status |
| Staff | Chat with assistants for their clients, upload PHI documents for extraction inside a conversation |

**A staff member's day**

1. Logs in and sees only the assistants for clients they belong to.
2. Opens "Client A Assistant". Conversations list on the left, chat in the middle.
3. Asks: "What's the fax number for prior auth?" Within 1 to 3 seconds: the number, one line, with a source chip "Prior Auth Guide v2, p.12".
4. Asks a question whose answer is on a scanned page. Sees "Searching Client A documents...", then "Page 31 is a scanned image, reading it now...". About 10 seconds later: the answer, marked as read from an image, with the page thumbnail.
5. Clicks the source chip. A side panel opens the PDF at that page.
6. Uploads a patient fax and asks for all fields. Gets a field table beside the page image. Unreadable fields are blank and flagged, conflicting ones highlighted. This fax stays in this conversation only.
7. Asks the assistant to draft a follow-up email. It drafts it, without inventing client facts.
8. Asks something not in the documents. Gets: "I don't have that in Client A's documents."

**A client admin's flow**

1. Opens the assistant's Knowledge page: a plain list of files with status.
2. Uploads a PDF. Status moves through Queued, Reading, Scanning images, Ready.
3. If some pages are hard to read: "Ready (2 pages need review)". They can open those pages and see what was read.
4. Uploads a newer version of a file. Old chunks are retired, the new version becomes active, and history is kept for audit.

The interface uses Staffingly's brand palette and lives in a separate UI design spec, so this document stays about the engine.

## Core principle: a fixed engine with swappable parts

The engine (graph, evidence rules, data model, security) is locked. Every external part sits behind an interface and is chosen in a config file, so moving from free local models to paid frontier APIs changes configuration, not code.

**Locked in (changing these means a redesign or a migration)**

| Decision | Why it is locked | Cost to change later |
| --- | --- | --- |
| Python 3.12 + FastAPI backend | Whole engine, tests and Antigravity prompts assume it | Full rewrite |
| LangGraph for the question flow | Node, edge and state contract is the core of the engine | Rewrite of orchestration |
| PostgreSQL as the one system database (users, chunks, vectors, logs, queue, checkpoints) | One thing to secure, back up and isolate by tenant | Large migration |
| pgvector for vectors | Lives inside Postgres, tenant rules apply to it | Re-index everything |
| Next.js web app | Matches AEGIS, needed for a chat-style interface | Frontend rewrite |
| Page-first ingestion, page as the unit of evidence | Citations, retries and vision all depend on it | Re-ingest everything |
| The 15 invariants (next section) | They are the product's safety guarantees | Not negotiable |
| Embedding model per index version | Vectors from different models cannot be mixed | Re-embed all documents (planned, versioned) |

**Swappable (change by configuration)**

| Part | Interface | Today (local) | Later options |
| --- | --- | --- | --- |
| Router model | `ModelProvider` role `router` | Small Ollama model or rules | Gemini, Claude, GPT |
| Answer model | role `answer` | Ollama model | Gemini, Claude, GPT |
| Vision reader | role `vision` | Ollama vision model | Gemini, Claude, GPT vision |
| Field extractor | role `extract` | Ollama vision model | Strongest available vision model |
| Eval judge | role `judge` | Ollama model | A different provider than the answer model |
| Embeddings | `EmbeddingProvider` | BGE-M3 | Any, with a planned re-index |
| Reranker | `Reranker` | bge-reranker-v2-m3 | Any |
| Parser / OCR | `DocumentParser` | Docling | Google Document AI or others, if on the BAA list |
| File storage | `StorageBackend` | Local folder | Cloud Storage |
| Job queue | `JobQueue` | Procrastinate (in Postgres) | Cloud Tasks or other |
| Auth | `AuthProvider` | Built-in (hashed passwords, sessions) | Identity Platform or SSO |
| Tracing | `Tracer` | Postgres trace tables + optional Langfuse | Same, or a covered service |

Rule for the code: **no `if production:` branches inside the engine.** The engine calls `ModelProvider.generate(role="answer", ...)`, never a vendor name. Adding a new provider means writing one adapter class and one config entry.

## The 15 invariants

These rules hold in every environment and every model configuration. Each one has at least one automated test, and a build that breaks any of them does not ship.

| # | Invariant | Enforced by |
| --- | --- | --- |
| 1 | The model never has database credentials | Model adapters have no DB access; only the retrieval service does |
| 2 | Tenant authorization is enforced before retrieval | Session-derived `tenant_id` + Postgres row-level security |
| 3 | The model never decides access control | `tenant_id` and permissions never come from model output |
| 4 | Every knowledge answer has sufficient evidence or refuses | `assess_evidence` node; no path around it |
| 5 | Visual readings are derived evidence: always cited as visual, shown with the page image, never treated as equal to original text | `evidence_type` carried to the citation and UI |
| 6 | Unreadable fields get an explicit state, never a guess | Field state enum + false-fill tests |
| 7 | PHI uploaded in chat never enters the shared knowledge index unless explicitly promoted | Separate storage and index scope |
| 8 | Every answer traces to a document version and page | Evidence IDs include `document_version_id` and `page_id` |
| 9 | Component failure may degrade retrieval but never bypasses the evidence gate | Fallback paths re-enter `assess_evidence` |
| 10 | Operational telemetry never depends on PHI redaction working | Ops logs carry metadata only, no content |
| 11 | All ingestion steps are resumable and idempotent | Idempotency keys + unique constraints |
| 12 | Model providers are replaceable behind interfaces | `ModelProvider` roles + config |
| 13 | Every failure is traceable to one node and one request | `request_id` + per-node step records |
| 14 | No real PHI in local or staging environments | Synthetic and de-identified data only; environment checks |
| 15 | Duplicate detection and caching never cross client boundaries | Hashes and caches keyed by `tenant_id` |

## Proposed architecture

Seven layers, with the locked engine in the middle and every vendor pushed to the bottom behind interfaces.

**Layers, top to bottom**

1. **Users:** staff, client admin, platform admin.
2. **Web app (Next.js):** chat window, source panel with pdf.js, Knowledge page for uploads.
3. **API (FastAPI):** login and roles, rate limits, tenant taken from the session, streams status lines and answers.
4. **Fixed engine (locked):** query graph (LangGraph), ingestion jobs (page by page, idempotent), extraction (field schemas, dual read, validators), evidence gate (every answer passes it).
5. **Interfaces (`ports/`):** `ModelProvider`, `EmbeddingProvider`, `Reranker`, `DocumentParser`, `Retriever`, `StorageBackend`, `JobQueue`, `AuthProvider`, `Tracer`.
6. **Swappable adapters (chosen by config profile):** models (mock, Ollama, Vertex AI, frontier APIs), embeddings and reranker (BGE-M3, bge-reranker-v2-m3), parsing (Docling), storage and queue (local folder or Cloud Storage, Procrastinate).
7. **Data:** PostgreSQL + pgvector (tenants, documents, chunks, vectors, checkpoints, traces, audit, job queue, row-level security) and file storage (originals, page images, signed short-lived links).

Requests flow top to bottom. The engine talks only to interfaces, so swapping Ollama for Gemini or a frontier API, or a local folder for Cloud Storage, never touches engine code. Only the `Retriever` reads chunks from the database, and model adapters have no database access at all (invariant 1).

| Layer | Owns | Never does |
| --- | --- | --- |
| Web app | Chat, sources, uploads, status display | Business rules, direct database access |
| API | Authentication, roles, tenant context, rate limits, streaming | Retrieval logic |
| Engine | Routing, retrieval, evidence assessment, generation control, validation, ingestion, extraction | Import a vendor library |
| Interfaces | Contracts between engine and parts | Contain logic |
| Adapters | Talking to one vendor or tool each | Decide flow or access |
| Data | Persistence, tenant isolation (RLS), queue, traces | Trust the caller's tenant claim without a session |

## Query flow

A question passes nine LangGraph nodes, and the evidence gate in node 5 decides whether the model may answer at all.

**Nodes, in order**

| # | Node | What it does | Next |
| --- | --- | --- | --- |
| 1 | `guard_input` | Length limits, injection checks, rate limit | 2; if rejected, 9 with the reason |
| 2 | `route` | Picks the path (knowledge, general, extraction, clarify) and the answer mode | knowledge: 3. general: 7, no retrieval, may not state client facts. extraction: extraction path. clarify: 9 with the top 3 matching topics |
| 3 | `retrieve` | Hybrid search (vector + keyword), tenant-filtered, merged with RRF | 4 |
| 4 | `rerank` | Multilingual reranker | 5 |
| 5 | `assess_evidence` (the gate) | Sets `evidence_state`: `RELIABLE_TEXT`, `VISUAL_REQUIRED`, `CONFLICTING`, `WEAK` or `NO_EVIDENCE` | RELIABLE_TEXT: 7. VISUAL_REQUIRED: 6. CONFLICTING: 9 with both sources. WEAK or NO_EVIDENCE: 9 as a refusal |
| 6 | `vision_read` | Sends the page image and the question to the vision role | 7. If it fails: use OCR text if its confidence passes, otherwise refuse for that page |
| 7 | `generate` | Structured output in the fixed schema, token cap by answer mode | 8 |
| 8 | `validate` | Text: quote must match stored text exactly. Visual: compare question-time and upload-time readings. Table: value must be in the cited row. Citation IDs must exist. Tenant check | Pass: 9. Fail: back to 7 once. Second fail: 9 as a refusal |
| 9 | `respond` | Streams the answer, sources, evidence type and page thumbnail | End |
| any | error after retries | `safe_failure` | User message with a public reference |

**Extraction path:** `extract_fields` (OCR read and vision read) → `dual_read_compare` → `classify_confidence` → `validate_fields` → `respond` with the field table and states.

**Implementation notes**

- **Request-scoped context.** Question text and evidence text are loaded once per request into a context object that lives outside the checkpointed graph state. Checkpoints hold IDs only.
- **Visual comparison uses normalized values.** Two vision readings never match word for word, so compare the extracted values after normalizing them (digits, dates, IDs, amounts). Prose may differ; an ID may not.

If validation fails, generation is retried once; a second failure ends in a refusal. Any node error after its retries goes to `safe_failure`.

**Graph state (control fields only)**

`request_id`, `tenant_id`, `user_id`, `conversation_id`, `route`, `answer_mode`, `language`, `evidence_ids`, `evidence_state`, `needs_vision`, `status`, `public_error_ref`. Question text, evidence text and drafts are fetched by ID, never stored in state.

**Answer modes (chosen by the router before generation)**

| Mode | Example question | How the answer is produced |
| --- | --- | --- |
| `exact` | "What's the prior auth fax number?" | Stored span returned as written, minimal framing |
| `short` | "Does Client A accept Aetna?" | One or two sentences, hard token cap |
| `explain` | "How does the referral process work?" | Summary from evidence, still cited |
| `table` | "What's the rate for code X?" | Lookup in stored table rows, cites sheet and row |

The model returns JSON in a fixed schema (Vertex AI supports response schemas), for example `{mode, answer, evidence_ids, quoted_span, evidence_type}`. Code renders it, so the model cannot pad the answer.

**Validation by evidence type**

- **Text:** `quoted_span` must exist character for character in the stored text.
- **Visual:** the question-time reading is compared with the upload-time reading of the same page (and OCR text if present). Disagreement flags the answer. The page thumbnail is always shown, because no automatic check fully proves a value read from an image.
- **Table:** the cited row must contain the value.

**Language.** The answer follows the user's language; quoted source text stays in its original language, labelled, for example "Source (English)".

## Ingestion pipeline

Ingestion turns every upload into page-level evidence, and image pages get a vision reading at upload so search can find them later.

**Steps**

1. **Upload:** the client admin uploads from the Knowledge page.
2. **Check file:** type checked by content, size and page limits, malware scan, SHA-256 hash compared within the same tenant only.
3. **Split pages:** render each page as an image. Each page becomes its own job.
4. **Classify page:** digital text, scanned, table-heavy or image-heavy, with OCR confidence.
5. **Extract by page type (three paths):** digital pages go to Docling text and tables; scanned pages go to Docling OCR with confidence; image-heavy or low-OCR-confidence pages get a vision reading at upload.
6. **Store readings:** every reading kept separately in `page_readings`, language detected per page, `visual` flag set.
7. **Chunk + context:** structure-aware chunks, tables kept whole, document context added to each chunk.
8. **Embed + index:** BGE-M3 vectors and the full-text index, tagged with parser, chunker and embedding versions.

**End states:** **Ready** (searchable, citations by page) or **Needs review** (low-confidence pages shown to the client admin; the rest of the file stays usable).

Ingestion runs as background jobs, not LangGraph: its flow is fixed, with no model deciding the next step, so plain jobs are simpler to retry and debug.

**Why vision runs at upload.** Search cannot find a page nobody has read. An image page with no text is invisible to vector and keyword search, so the question-time vision step would never be reached. The upload-time reading makes the page findable; the question-time reading answers precisely. VisRAG research supports reading pages as images, and its follow-up work warns that visual RAG still hallucinates, which is why both readings are compared.

**Production rules**

- Each step's idempotency key is `document_version_id + page_number + step + processing_version`, enforced by a unique constraint.
- File and page hashes are compared only within the same tenant (invariant 15).
- A new version of a file becomes active only when all its pages are processed or marked for review; the old version's chunks are then retired, not deleted, for audit.
- CSV and Excel files skip chunking and are stored as tables for direct lookup.
- Changing the parser, chunker or embedding model bumps `processing_version`, which shows exactly which pages need reprocessing.

## Model roles and runtime profiles

The engine asks for a role, never a model. A runtime profile maps each role to a provider, so the same code runs on the office PC with no GPU, the RTX 3060 PC, and GCP, and later on paid frontier APIs.

**Model roles**

| Role | Job | What it needs |
| --- | --- | --- |
| `router` | Classify the question, pick the answer mode | Fast and cheap. Can start as rules plus a small model |
| `answer` | Write the reply from validated evidence | Instruction following, structured output |
| `vision` | Read page images at upload and at question time | Strong document reading |
| `extract` | Fill PHI field schemas | Highest accuracy, structured output |
| `embed` | Turn text into vectors | Multilingual (BGE-M3) |
| `rerank` | Order retrieved candidates | Multilingual |
| `judge` | Score answers during evaluation only | Different from the answer model, so it does not grade its own work |

**Runtime profiles**

| Profile | Where | Generation models | Use it for |
| --- | --- | --- | --- |
| `mock` | Any PC, no model at all | Deterministic fake provider returning fixed outputs | Building and testing the engine: graph, database, API, UI, fallbacks. Instant and free |
| `office-cpu` | Office PC, no GPU | Small Ollama models on CPU (for example a 4B text model, a 2B vision model) | Real end-to-end runs on short synthetic documents. Slow, especially vision |
| `home-gpu` | RTX 3060, 12 GB | Larger Ollama models on GPU (for example an 8B text model plus a 4B vision model) | Quality testing and the evaluation set |
| `dev-free-api` | Any PC | Gemini free tier, **synthetic data only** | Checking quality against a stronger model at $0 |
| `cloud` | GCP | Gemini on Vertex AI under the BAA | Staging and production |
| `frontier` (later) | GCP | Paid Claude, GPT or Gemini models, each under its own BAA | Roles where measurements show a stronger model pays off |

**The office PC, honestly.** Ollama runs on CPU, but generation is much slower than on a GPU, and vision reads of scanned pages will be the slowest step. That is fine, because most engine work does not need a real model: the `mock` profile covers the graph, data model, security, fallbacks and UI. Real-model runs on the office PC should use short synthetic documents. Embeddings (BGE-M3) and the reranker also run on CPU, slower during ingestion but workable for development.

**On a 12 GB GPU, models compete for memory.** Ollama keeps several models loaded only if they all fit in VRAM; otherwise it unloads idle ones and queues requests until the needed model loads. So the `home-gpu` profile keeps the generation models small enough to sit together, and runs embeddings and the reranker on CPU. Confirm what is actually loaded with `ollama ps`.

**Desktop GPU, the default for real-model work.** Office desktop: i7-12700F, 32 GB RAM, RTX 3050 with 6 GB. Profile `desktop-gpu`: one model, `qwen3-vl:4b`, for both the answer and vision roles, so nothing swaps inside 6 GB of VRAM; embeddings and reranker run on CPU. Scale page images down before vision reads to keep memory in bounds. The 8 GB office PC (`office-cpu`, no GPU) is for mock-profile work only, with Docker capped at 3 GB.

**Free-tier rule.** The Gemini free tier may use prompts to improve Google's products and is not covered by any BAA. It is allowed only for synthetic test data (invariant 14), never for real documents or PHI.

**Example profile file**

```yaml
profile: office-cpu
models:
  router:  { provider: ollama, model: "<small text model>" }
  answer:  { provider: ollama, model: "<small text model>" }
  vision:  { provider: ollama, model: "<small vision model>" }
  extract: { provider: ollama, model: "<small vision model>" }
  judge:   { provider: ollama, model: "<different small model>" }
embeddings: { provider: local, model: "BAAI/bge-m3", device: cpu }
reranker:   { provider: local, model: "BAAI/bge-reranker-v2-m3", device: cpu }
storage:    { backend: local, path: ./data/files }
queue:      { backend: procrastinate }
```

Switching to frontier models later means adding one adapter (for example `AnthropicProvider`) and editing this file. Every role is evaluated separately, so a model change is accepted only if that role's scores hold or improve.

## Tech stack

Everything local is free and open source; each piece maps to a GCP service behind the same interface. Pin exact versions in the repository before the first build.

| Layer | Local ($0) | GCP | Reason |
| --- | --- | --- | --- |
| Web app | Next.js + Tailwind | Cloud Run | Chat-style interface; matches AEGIS |
| PDF viewer | pdf.js, embedded | Same | Opens at the cited page the same way in every browser |
| API | FastAPI (async), streaming over SSE | Cloud Run | Python, streams status lines and tokens |
| Orchestration | LangGraph + LangChain model wrappers | Same | Nodes, edges and state as the engine contract |
| Graph checkpoints | LangGraph Postgres checkpointer | Same, on Cloud SQL | Crash recovery per conversation; holds IDs, not content |
| Database | PostgreSQL + pgvector (Docker) | Cloud SQL for PostgreSQL | One database, one security model |
| Keyword search | Postgres full-text search | Same (BM25 extension available on Cloud SQL) | Identical behaviour in both places |
| Background jobs | Procrastinate (queue stored in Postgres) | Same | No Redis to run or pay for |
| File storage | Local folder | Cloud Storage | Behind `StorageBackend` |
| Parsing and OCR | Docling | Same | PDF, Office files and images; runs locally |
| Embeddings | BGE-M3 | Same model, self-hosted | Multilingual; avoids a re-embed when moving to cloud |
| Reranker | bge-reranker-v2-m3 | Same | Multilingual |
| Generation models | Ollama (see profiles) | Gemini on Vertex AI | Swappable by role |
| Data migrations | Alembic | Same | Safe schema changes |
| Secrets | `.env` (never committed) | Secret Manager | No secrets in code |
| Log redaction safety net | Microsoft Presidio | Same | Scrubs exception text; not the primary PHI control |
| Dev tracing | Langfuse, self-hosted in Docker, synthetic data only | Off at first | Visual node-by-node debugging |
| Evaluation | pytest + RAGAS + our own metrics | Same, in CI | Every change is scored |
| Load testing | Locust | Same | Capacity curve before real users |

**Notes from checking each choice**

- **Procrastinate** is a Python task queue that uses PostgreSQL for tasks, locks and retries. Its maintainers say they are looking for more maintainers, so it stays behind the `JobQueue` interface.
- **Langfuse** is MIT-licensed and free to self-host, but version 3 needs Postgres, ClickHouse, Redis and object storage together. Fine in local Docker, too heavy for the pilot budget in cloud.
- **Gemini through Vertex AI**, not Google AI Studio API keys, is the BAA-covered route. Google's own migration guide tells regulated customers to use Vertex AI.
- Every GCP service used with PHI must appear on Google's current HIPAA covered-products list at the time we deploy.

## Data model

Every row that holds client data carries `tenant_id`, and every piece of evidence can be traced down the chain tenant, document, version, page, chunk.

**Core tables**

| Table | Key columns | Notes |
| --- | --- | --- |
| `tenants` | `tenant_id`, name, status | One per client |
| `assistants` | `assistant_id`, `tenant_id`, name, settings | A client can have more than one assistant later |
| `users` | `user_id`, email, password hash, status | Platform users |
| `memberships` | `user_id`, `tenant_id`, role | Role per client: client\_admin or staff |
| `documents` | `document_id`, `tenant_id`, title, `active_version_id` | Logical document |
| `document_versions` | `document_version_id`, `document_id`, `sha256`, uploaded\_by, status, created\_at | Each upload is a version; hash checked per tenant only |
| `pages` | `page_id`, `document_version_id`, `page_number`, `page_sha256`, `page_type`, `language`, `ocr_confidence`, `visual`, image path, review\_status | The unit of evidence |
| `page_readings` | `page_id`, source (ocr, vision\_ingest), text, field values, model and version | Every reading of a page, kept separately |
| `chunks` | `chunk_id`, `page_id`, `tenant_id`, text, context prefix, `embedding`, full-text vector, `chunker_version`, `embedding_model`, `embedding_version`, `parser_version` | Active only for the active version |
| `tables_data` | `table_id`, `document_version_id`, sheet, rows (JSON) | CSV and Excel stored as tables, not vectors |
| `conversations` | `conversation_id`, `tenant_id`, `user_id`, `assistant_id` |  |
| `messages` | `message_id`, `conversation_id`, role, content, answer\_mode, created\_at | Content lives here once |
| `answer_citations` | `message_id`, `chunk_id` or `page_id`, `evidence_type` (text, visual, table) | Invariant 8 |
| `session_documents` | `session_document_id`, `conversation_id`, file, pages, retention\_until | PHI uploads in chat; never indexed in `chunks` |
| `extraction_results` | `session_document_id`, schema, fields with state and confidence |  |
| `jobs` / `job_steps` | idempotency key, status, attempts, error | Ingestion progress per page and step |
| `run_steps` | `request_id`, node, status, latency, model, prompt version, evidence ids, error | Restricted trace store |
| `audit_events` | actor, action, object, time | Append-only |
| `feedback` | `message_id`, rating, reason | Feeds the evaluation set |

**Field states for extraction**

| State | Meaning |
| --- | --- |
| `FILLED_HIGH` | Readings agree and validators pass |
| `FILLED_MEDIUM` | One reading only, or low confidence; shown for checking |
| `CONFLICTING` | Readings disagree; both values shown |
| `UNREADABLE` | Field exists but cannot be read |
| `INVALID` | Read, but fails a validator (date, ID format, NPI check digit) |
| `NOT_PRESENT` | Field does not appear on the document |
| `NOT_APPLICABLE` | Field does not apply to this document type |

**Idempotency.** Each ingestion step has the key `document_version_id + page_number + step + processing_version`, backed by a unique constraint. A retry that finds the key already done reuses the result, so no duplicate vectors appear.

## Security, HIPAA and guardrails

PHI is in scope, so security is designed in from the first commit, not added at deployment. A signed BAA covers Google's side only; HIPAA compliance also depends on how we configure, control access to and log every service.

**Compliance basics**

- Use only BAA-covered services for anything that touches real data: Vertex AI (not AI Studio keys), Cloud SQL, Cloud Storage, Cloud Run, Cloud Logging. Confirm each against Google's current covered-products list before go-live.
- Staffingly needs its own BAA with each client practice.
- Any frontier provider added later (Anthropic, OpenAI) needs its own BAA on a qualifying plan before real data reaches it.
- No PHI goes to third-party tracing or evaluation services.
- Embeddings of PHI are treated as PHI.
- Encryption at rest and in transit; signed, short-lived links for every document and page image.
- Retention and deletion rules for messages, session uploads, checkpoints and traces, agreed with Dan before go-live.

**Access control**

- `tenant_id` comes only from the authenticated session.
- Postgres row-level security on every tenant table, as a second wall behind application checks.
- The retrieval service connects with a read-only database role.
- Roles checked on every API route (platform admin, client admin, staff).

**Guardrails by stage**

| Stage | Guardrails |
| --- | --- |
| Login and API | Rate limits per user and per tenant, session expiry, failed-login lockout |
| Upload | File type checked by content (not extension), size and page limits, malware scan, per-tenant hashing |
| Input | Length limits, prompt-injection pattern checks, language detection |
| Retrieval | Tenant filter enforced in the database; document text always passed as data, never as instructions |
| Generation | Response schema only, output token cap per answer mode |
| Output | Quote check for text evidence, visual-evidence labelling, citation ID check, cross-tenant check, refusal when evidence is weak |
| General path | May draft and explain; may not state facts about a client, payer rule or clinical matter |
| Cost | Daily spend cap per tenant in cloud, with alerts |

These map to the OWASP Top 10 for LLM applications (2025), especially prompt injection, sensitive information disclosure, excessive agency, vector and embedding weaknesses, and unbounded consumption.

## Logging and observability

Everything is logged, in three separate stores, and one `request_id` ties them together. PHI is kept out of operational logs by design, not by filtering.

| Store | What it holds | Contains PHI? | Where |
| --- | --- | --- | --- |
| Operational logs | Request, node, status, latency, model name, counts, error codes. **No questions, answers, prompts or document text** | No (by design) | JSON to file locally, Cloud Logging in GCP |
| Trace store (`run_steps`) | Per node: inputs and outputs by ID, model, prompt version, tokens, latency, evidence IDs, errors | Yes | Postgres, encrypted, admin-only |
| Audit log (`audit_events`) | Who viewed, uploaded, exported or deleted what, and when | References only | Postgres, append-only |

**Example operational log line**

```json
{"ts":"2026-10-01T10:15:02Z","request_id":"req_01J...","tenant_id":"t_042","node":"retrieve","status":"ok","latency_ms":184,"candidates":24,"route":"knowledge","profile":"office-cpu"}
```

**Rules**

- Presidio scrubs exception messages and stack traces before they reach operational logs, because those can carry data by accident. It is a safety net, not the control.
- Every node writes a `run_steps` record on entry, exit and failure, so the failing layer is visible without reading raw logs.
- Every answer records the prompt version and model version that produced it.
- The user-facing error reference (for example `ERR-7K42`) is random and separate from the internal `request_id`.
- Langfuse runs only locally with synthetic data, for visual debugging of the graph.

## Failure handling and fallbacks

A failed component may make retrieval weaker, but every knowledge answer still goes through the evidence gate (invariant 9). If the gate is not satisfied, the system refuses.

| Failure | Fallback | What the user sees |
| --- | --- | --- |
| Model timeout | Node retry with backoff (LangGraph retry policy) | Nothing, if the retry succeeds |
| Model down after retries | `safe_failure` node | "I'm having trouble right now. Reference: ERR-7K42" |
| Vector search fails | Keyword search only, then rerank, then the evidence gate | An answer only if evidence is still strong, otherwise a refusal |
| Keyword search fails | Vector search only, then the evidence gate | Same rule |
| Reranker fails | Merged results unranked, then the evidence gate | Same rule |
| Vision read fails on a page | Use OCR text if its confidence passes; otherwise exclude the page | "Page 31 couldn't be read clearly. Open the source to check." |
| Validation fails | Regenerate once, then refuse | "I couldn't confirm that from the documents." |
| Evidence conflicting | Show both sources, no single answer | "Two documents disagree:" with both citations |
| Evidence weak or missing | Refuse | "I don't have that in Client A's documents." |
| Ingestion step fails on a page | Retry that page and step only; then mark for review | "Ready (1 page needs review)" |
| Database unavailable | Fail closed, no answer from model memory | Error with reference |
| Process crash mid-question | Resume from the last checkpoint by `thread_id` | The answer arrives, slightly late |

**Checkpoint design.** The LangGraph checkpointer saves state after every node. State therefore holds IDs and control fields only (request, tenant, user, conversation, route, answer mode, evidence IDs, status), never question text, evidence text or drafts. Content is fetched by ID from its own store. This keeps checkpoints small and stops PHI being copied into every snapshot. The checkpointer's restriction to known-safe types when loading checkpoints stays switched on.

## Evaluation

No change ships unless the evaluation suite passes. The golden set is synthetic or de-identified, built to look like real client material: multi-page PDFs with embedded images, scanned faxes, tables, Spanish and English.

**Metrics**

| Area | Metric | Gate type |
| --- | --- | --- |
| Retrieval | Hit@5, rank of the correct page | Must not drop |
| Visual retrieval | Hit@5 on visual-only pages | Must not drop |
| Routing | % of questions sent to the right path | Must not drop |
| Answers | Faithfulness (RAGAS), exact match in exact mode | Must not drop |
| Citations | Citation precision and citation recall | Must not drop |
| Refusals | Correct refusals and wrong refusals | Must not drop |
| Vision answers | Accuracy on visual-recovery questions | Must not drop |
| Extraction | Field exact match, false-fill rate, correct state per field | False-fill must not rise |
| Security | Cross-tenant leaks, injection success rate | **Must be zero** |
| Speed | p50 and p95, text path and vision path separately | Report |
| Cost | Per question, per page ingested | Report |

**Test categories**

- `TEXT_FACT`: answer is in normal text.
- `VISUAL_RECOVERY`: scanned page, image-heavy page, bad OCR, table screenshot, fax, handwriting, mixed text and image, multilingual scan.
- `TABLE_LOOKUP`: answers from CSV or Excel.
- `NO_ANSWER`: must refuse.
- `CONFLICT`: two sources disagree.
- `MULTILINGUAL`: Spanish question on English source, and the reverse.
- `LENGTH`: one-line question must get a one-line answer.
- `EXTRACTION`: field tables, including deliberately unreadable fields.
- `CROSS_TENANT` (adversarial): same file names, same patient names, same wording across two tenants. Any leak fails the build.
- `INJECTION` (adversarial): instructions hidden inside uploaded documents.

**Load testing.** Define the target as N concurrent requests at p95 latency X with error rate below Y, then run steps of 5, 10, 20, 50 and 100 users to draw a capacity curve.

**Judge caution.** RAGAS uses a model as judge and inherits its biases. On local profiles the judge is a small model, so hand-check a sample of every run.

## Repository structure and interfaces

One repository, with the engine in `core/` depending only on interfaces in `ports/`; every vendor lives in `adapters/`.

```text
chatbots-project/
  AGENTS.md             # rules Antigravity loads every session
  .agents/
    rules/              # invariants, architecture, security-phi, coding
    workflows/          # /next-task, /review-task, /close-task
  backend/
    app/
      api/            # FastAPI routes, auth, SSE streaming
      core/
        graph/        # LangGraph: state, nodes, edges
        retrieval/    # hybrid search, RRF, evidence assessment
        ingestion/    # page pipeline steps
        extraction/   # field schemas, reconciliation, validators
        guards/       # input, output, cross-tenant checks
      ports/          # interfaces only (no vendor imports)
      adapters/
        models/       # mock, ollama, vertex, (later) anthropic, openai
        storage/      # local, gcs
        parsing/      # docling
        queue/        # procrastinate
      db/             # SQLAlchemy models, Alembic migrations, RLS policies
      observability/  # ops logger, run_steps writer, audit writer
    config/
      profiles/       # mock, office-cpu, desktop-gpu, home-gpu, cloud
    tests/
      unit/  integration/  invariants/  eval/
  prompts/
    registry.yaml       # every prompt with id and version
  evals/
    golden/             # synthetic documents + labelled questions
    reports/            # saved evaluation results
  web/                  # Next.js app
  docker-compose.yml    # postgres(pgvector), api, worker, web, langfuse (optional)
  docs/
    phases/  decisions/  STATUS.md  BUILD_LOG.md  FAILURE_MODES.md  OPEN_QUESTIONS.md
```

**Interfaces (in `ports/`)**

| Interface | Main methods |
| --- | --- |
| `ModelProvider` | `generate(role, messages, schema, max_tokens)`, `read_page(role, image, question)`, `extract_fields(role, image, schema)` |
| `EmbeddingProvider` | `embed(texts) -> vectors`, `model_id`, `version` |
| `Reranker` | `rerank(query, candidates) -> ordered` |
| `DocumentParser` | `split_pages(file)`, `extract(page) -> text, tables, confidence` |
| `StorageBackend` | `put`, `get`, `signed_url(ttl)`, `delete` |
| `JobQueue` | `enqueue(task, key)`, `status(key)` |
| `AuthProvider` | `authenticate`, `session`, `roles(user, tenant)` |
| `Tracer` | `step_start`, `step_end`, `step_error` |
| `Retriever` | `search(tenant_ctx, query, filters) -> evidence ids` (the only component with database read access to chunks) |

Architecture decisions (for example "why Procrastinate") are recorded as short ADR files in `docs/`, so the reasoning survives handover.

## Build plan

We build in eight phases. Phase 1 may run in parallel with Phase 0, because it uses only the mock profile and synthetic data; every other phase starts only after the previous gate passes. Phases 1 to 5 run on our own PCs at $0; money is spent only from Phase 6.

| Phase | Name | What gets built | Exit gate | Runs on |
| --- | --- | --- | --- | --- |
| 0 | Foundations | Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec | Compliance route confirmed in writing | Planning, no machine needed |
| 1 | Engine on mock profile | Repo, schema with RLS, auth and roles, ports, mock provider with failure injection, LangGraph skeleton with all 9 nodes, three log stores, invariant tests, import rules, graph contract test, failure-mode tests, pre-commit checks, LangGraph Studio with tracing off | Every invariant and failure-mode test passes on the mock profile | 8 GB office PC |
| 2 | Ingestion | Page-first pipeline, Docling, page classification, vision at upload, idempotent jobs, versions and hashes, Knowledge page | Golden documents ingested twice with zero duplicates | Office PC (mock), then desktop-gpu |
| 3 | Retrieval and answers | Hybrid search, rerank, evidence gate, answer modes, structured output, validation, citations, chat UI with streaming status lines and the source panel | Baseline metrics recorded, zero cross-tenant leaks | desktop-gpu. **Demo for Dan here** |
| 4 | Vision and extraction | Question-time vision, field schemas, dual read, field states, validators, extraction table beside the page image | False-fill and visual recovery baselines recorded | desktop-gpu |
| 5 | Hardening | Guardrails, injection suite, cross-tenant adversarial suite, rate limits, Presidio scrubbing, feedback buttons, capacity curve, backups with a tested restore | Security suite passes, capacity curve drawn | desktop-gpu |
| 6 | Cloud staging on GCP | Cloud SQL, Cloud Storage, Cloud Run, Vertex AI, Secret Manager; same test suite on the cloud profile, synthetic data only | Scores hold on cloud, monthly cost measured | GCP |
| 7 | Pilot with one client | Real data under the BAA, side by side with Chatbase for two weeks, then the remaining clients | Dan signs off and Chatbase is cancelled | GCP |

**Fixed ordering rules**

- The chat UI is built in Phase 3, not later, so the first working demo comes at the end of Phase 3.
- Security, cross-tenant and load testing happen in Phase 5, before any cloud deployment or real data. They never move to the pilot.
- No admin dashboards. The Knowledge page and its review list are the only admin screens.

Durations are not set yet; they depend on Antigravity's pace and review time. Phases 1 and most of 2 to 5 work on the `mock` profile first, then on real local models, so the office PC is enough for most of the build.

**First tasks (Phase 0)**

- [ ] Raise the pharmacy system's Gemini key with Dan and compliance; plan the Vertex AI move
- [ ] Agree retention periods and the pilot workload
- [ ] Pick the first pilot client and collect a de-identified document sample
- [ ] Build 20 to 30 synthetic documents covering every test category
- [ ] Write 100+ labelled questions against them
- [ ] Create the repository skeleton and pin versions
- [ ] Write the UI design spec with the brand palette

**Definition of Done for every module**

- [ ] Spec section, interface and tests written before the code
- [ ] Unit, integration and invariant tests pass
- [ ] Evaluation suite run; no gated metric dropped
- [ ] Ops logs and `run_steps` records in the agreed format
- [ ] No vendor import inside `core/`
- [ ] ADR written for any decision the spec did not cover
- [ ] Reviewed by Srinath before the next module starts

## Working with Antigravity

Antigravity implements; it does not design. It has made autonomous choices before (industry lists, title priority, batch sizes) when not boxed in, so every task is given as spec, then interface, then tests, then implementation.

**Rules for every task**

1. One module per task, with its section of this spec pasted in.
2. Interfaces and tests are written and reviewed first. Implementation starts only when the tests exist and fail.
3. Pin every library version. No new dependency without asking.
4. No vendor names inside `core/`. Vendors live only in `adapters/`.
5. Never weaken a test or an invariant to make a build pass. Report the conflict instead.
6. Every new node writes `run_steps` and operational logs in the agreed format.
7. All test data is synthetic. No real documents in the repository, ever.
8. Each module ends with a short review: what was built, what was decided, what is unclear. Decisions go into an ADR.
9. Stop and ask when the spec is silent. Do not fill gaps with assumptions.

**Review checklist before a module is accepted**

- [ ] Tests pass, including all invariant tests
- [ ] No `if production:` branches in the engine
- [ ] Tenant filter present on every query, RLS policy present on every tenant table
- [ ] No content in operational logs
- [ ] Versions pinned, ADR written for any new decision

## Risks and open decisions

The biggest risk is not technical: it is building before compliance and the pilot workload are agreed.

**Risks**

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Office PC too slow for real-model runs | Slow development loop | `mock` profile for engine work; real runs on the GPU PC or with the free API on synthetic data |
| Small local models give weak answers | Local quality looks worse than production will be | Judge quality on `home-gpu` and `dev-free-api`; never tune the engine around a weak model's quirks |
| Vision reads hallucinate plausible values | Wrong field or wrong answer | Two independent reads, validators, visual labelling, mandatory page thumbnail |
| Antigravity drifts from the spec | Hidden design changes | Tests first, module-by-module review, ADRs |
| Procrastinate maintenance | Library stalls | Behind `JobQueue` interface; swappable |
| Cloud cost exceeds target | Budget overrun | Measure on the pilot workload first; spend caps per tenant |
| A service is missing from the BAA list | Compliance gap | Check Google's covered list before each new service |
| Existing pharmacy extraction system uses an AI Studio key | Possible compliance gap in a live system | Confirm with compliance in writing and move to Vertex AI; separate from this build |

**Open decisions**

- [ ] Retention periods for messages, session uploads, checkpoints and traces (Dan and compliance)
- [ ] Pilot workload definition: users, questions per day, documents and pages per month
- [ ] First pilot client and their document sample (de-identified)
- [ ] Per-tenant spend cap in cloud
- [ ] Whether the office network allows using the GPU PC as a model server for the office PC (IT check)
- [ ] Authentication in production: built-in or Identity Platform (check BAA coverage)
- [ ] PHI document schemas for the first extraction types

## Sources

Checked during research in September and October 2026. Re-check versions and compliance lists before building and before go-live.

- [Google: migrate from AI Studio to Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/migrate/migrate-google-ai) (regulated customers should use Vertex AI)
- [Paubox: getting a BAA for your AI stack](https://www.paubox.com/blog/how-to-get-a-baa-for-your-ai-stack)
- [Vertex AI structured output](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/control-generated-output)
- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [Anthropic: Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- [VisRAG paper](https://arxiv.org/abs/2410.10594) and [VisRAG on GitHub](https://github.com/OpenBMB/VisRAG)
- [VisRAG 2.0: visual hallucinations in visual RAG](https://arxiv.org/pdf/2510.09733)
- [ColPali reproducibility study](https://arxiv.org/pdf/2505.07730) (performance drops as corpus grows)
- [BGE-M3 model card](https://huggingface.co/BAAI/bge-m3)
- [LangGraph Postgres checkpointer](https://pypi.org/project/langgraph-checkpoint-postgres/3.1.2/)
- [Procrastinate](https://procrastinate.readthedocs.io)
- [Langfuse self-hosting](https://js-sdk-v4-docs-snapshot.langfuse.com/self-hosting/)
- [Ollama FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.md) and [qwen3-vl in the Ollama library](https://registry.ollama.ai/library/qwen3-vl)
- [Microsoft Presidio analyzer](https://pypi.org/project/presidio-analyzer/1.10.0)
- [RAGFlow (reference for page parsing and template chunking)](https://deepwiki.com/infiniflow/ragflow)
- [Seven Failure Points When Engineering a RAG System](https://arxiv.org/pdf/2401.05856)
- [OWASP Top 10 for LLM Applications 2025, summary](https://www.promptfoo.dev/blog/owasp-top-10-llms-tldr/)
- [Linking to a PDF page with #page=](https://acrobatusers.com/tutorials/can-i-hyperlink-specific-page-pdf-file/) and [pdf.js #page support](https://github.com/mozilla/pdf.js/issues/1875)

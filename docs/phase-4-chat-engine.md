# Phase 4 — Chat Engine Module (1.c)

> **Goal:** Build the real-time conversational AI engine with context-aware retrieval, LLM fallback, and persistent conversation history.

---

## Scope

| Feature ID | Feature | Source Doc |
|------------|---------|------------|
| 1.c.a | Context-Aware Reply | `docs-2/context‑aware-reply.md` |
| 1.c.b | Fallback to LLM | `docs-2/fallback-to-llm.md` |
| 1.c.c | Conversation History | `docs-2/conversation-history.md` |
| — | Module Overview | `docs-2/chat-engine.md` |

---

## Deliverables

### 4.1 Database Migrations

| Migration | Script | Creates |
|-----------|--------|---------|
| 013 | `013-create-conversations.sql` | `conversations` table (UUID PK, tenant_id, user_id, started_at, ended_at) |
| 014 | `014-create-messages.sql` | `messages` table partitioned horizontally by month (conversation_id FK, role ENUM, content TEXT, created_at, citations JSONB, fallback_used BOOLEAN, idempotency_key VARCHAR) |
| 015 | `015-create-usage-metrics.sql` | `usage_metrics` table (tenant_id, date, messages_sent, tokens_used) |
| 016 | `016-add-message-indexes.sql` | GIN index on `citations`, unique partial index on `(tenant_id, idempotency_key)` |

### 4.2 Chat Engine Service (NestJS)

| Method | Path | Handler |
|--------|------|---------|
| `POST` | `/api/v1/chat/{conversationId}/message` | Full context-aware reply flow (see below) |
| `GET` | `/api/v1/chat/{conversationId}/messages` | Cursor-based pagination of conversation history |
| `POST` | `/api/v1/chat/{conversationId}/reset` | End current conversation, create new one |

#### Context-Aware Reply Flow (POST /message)

```
1. Idempotency Check → return cached response if key matches
2. Feature-Flag Guard → 403 if chatEngine.enabled = false
3. Persist User Message → messages (role='user')
4. Load Conversation Context → last N messages
5. Semantic Search → Milvus vector similarity (top-k)
6. Keyword Boost → ElasticSearch BM25 on same query
7. Merge & Build Citations → deduplicate, select top-M citations
8. Construct Prompt → system prompt + citations + history
9. Primary LLM Call → Diffusion-LLM inference
10. Fallback Decision:
    - If confidence < threshold OR primary fails → call Fallback LLM (OpenAI/Anthropic)
    - Set fallbackUsed = true
11. Persist Assistant Message → messages (role='assistant', citations, fallback_used)
12. Update Usage Metrics → increment messages_sent, tokens_used
13. Emit ChatMessageCreated → Kafka event
14. Return AssistantMessageResponse
```

### 4.3 LLM Integration Layer

| Component | Purpose |
|-----------|---------|
| **Primary LLM Client** | gRPC/HTTP client for Diffusion-LLM inference service |
| **Fallback LLM Client** | REST clients for OpenAI (`gpt-4o`) and Anthropic (`claude-3`) APIs |
| **Confidence Evaluator** | Extracts confidence score from primary response; compares against `fallback.confidenceThreshold` (default 0.65) |
| **Prompt Builder** | Assembles system prompt + retrieved citations + conversation history |

### 4.4 Conversation History & Data Lifecycle
- Cursor-based pagination (`limit` default 20, max 100; `cursor` = message UUID)
- Query: `WHERE conversation_id = :id AND created_at < cursor_created_at ORDER BY created_at DESC LIMIT :limit`
- Each message includes `citations` (JSONB) and `fallbackUsed` (boolean)
- `POST /reset` ends current conversation + creates a new one atomically
- **Cold Storage Archival Job**: A nightly Kubernetes CronJob scans table partitions for data older than the tenant's data retention limit (e.g. 1 year). Partitions are exported to Parquet format, uploaded to an S3 Glacier vault for compliance, and then the partition is `DROP`ped from PostgreSQL to prevent primary database bloat.

### 4.5 Idempotency
- `Idempotency-Key` header → SHA-256 hashed → stored in `messages.idempotency_key`
- Unique partial index prevents duplicates per tenant
- Duplicate requests return original response without new DB writes

### 4.6 Feature Flags (stored in `system_settings.feature_flags`)

| Flag | Type | Default | Controls |
|------|------|---------|----------|
| `chatEngine.enabled` | boolean | `true` | Enable/disable entire chat module per tenant |
| `fallback.enabled` | boolean | `true` | Allow fallback LLM usage |
| `fallback.confidenceThreshold` | float | `0.65` | Min confidence to avoid fallback |
| `feature.contextAwareReply` | boolean | `true` | Enable retrieval + citations |
| `conversationHistory.enabled` | boolean | `true` | Allow history access |

### 4.7 Observability
- Metrics: `chat_request_latency_seconds`, `chat_fallback_total`, `chat_error_total`
- SLO: 99.9% of chat replies under 500 ms
- Alert: fallback rate > 15% for 5 min → PagerDuty
- Grafana dashboard: per-tenant `messages_sent`, `tokens_used`, fallback ratio

### 4.8 Frontend — Chat Interface
- Real-time chat window with message bubbles
- Citation display (expandable snippets with document links)
- Fallback indicator badge
- "New Chat" button (calls reset endpoint)
- Infinite scroll for conversation history
- Loading skeleton during API calls

---

## Acceptance Criteria
- [ ] User sends message and receives context-aware reply with citations
- [ ] Citations correctly reference document chunks from the user's tenant
- [ ] Fallback triggers when primary LLM confidence is below threshold
- [ ] Fallback triggers when primary LLM is unavailable
- [ ] Response includes `fallbackUsed: true` when fallback is used
- [ ] Idempotent POST prevents duplicate messages on retry
- [ ] Conversation history loads with cursor-based pagination
- [ ] Conversation reset creates a new conversation and archives the old one
- [ ] Usage metrics updated after each assistant reply
- [ ] Feature flags correctly enable/disable each capability
- [ ] All tests pass: unit, integration, contract, E2E, performance, chaos

---

## Dependencies
- **Phase 1:** Kubernetes, PostgreSQL, Redis, API Gateway
- **Phase 2:** User authentication (JWT for all endpoints)
- **Phase 3:** Document indexing pipeline, Search Service, Milvus, ElasticSearch

## Risk Mitigation
| Risk | Mitigation |
|------|------------|
| Primary LLM outage | Automatic fallback to OpenAI/Anthropic |
| High latency from retrieval | Cache frequent queries in Redis; async pre-fetch |
| Token cost explosion | Per-tenant usage tracking + configurable limits |
| Both LLMs fail | Return 502 with clear message; PagerDuty alert |

---

*Estimated Duration: 3–4 weeks*

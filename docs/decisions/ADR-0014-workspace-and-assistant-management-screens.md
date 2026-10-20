# ADR-0014: Workspace and Assistant Management Screens

- **Status**: Accepted
- **Date**: 2026-10-20
- **Author**: Engineering Architecture Team / Antigravity

## Context & Problem Statement
The original build plan specified that the application would avoid administrative dashboards, designating the single "Knowledge page" as the sole administrative screen alongside a split chat interface (`Client Knowledge Assistant Architecture and Build Plan.md`, lines 78-80, 84-85, 93-100). 

In operational practice, tenants require multi-assistant partitioning per client (e.g. clinic-specific workflows, billing assistants, intake coordinators). Presenting assistants through an ad-hoc picker without dedicated management screens restricts administrative lifecycle control (configuration, instructions, safety guardrails, settings). Furthermore, staff users require a clear operational layout separating immediate consultation (Home chat) from transcript review (Conversations) and source document ingestion (Data sources).

This ADR formally records the product decision to adopt a Workspace + Multi-Assistant application layout inspired by modern assistant management architecture, while strictly preserving all security, tenant isolation, and evidence-grounding invariants.

## Decision Drivers
- **Tenant & Assistant Hierarchy**: A single client tenant workspace can host multiple assistants; staff must navigate assistants cleanly within their tenant boundary.
- **Strict Evidence Invariants (Invariants 1, 4, 6, 7)**: Assistant personalization must never bypass evidence-grounding rules or patient data safety boundaries.
- **Zero Third-Party PHI Leakage**: Tool calling and external actions touching patient data present severe security and HIPAA risks and must not be enabled without formal security design.
- **Elimination of Vanity Dashboards**: Retain the strict non-goal of vanity analytics (e.g., token consumption, user sentiment, retention stickiness curves). Screens serve direct operational workflows only.

## Decision Outcome
We replace the single-admin-screen constraint with a Workspace and Assistant Management Shell:

1. **Workspace Level**:
   - `/assistants`: Grid of assistant cards within the authenticated tenant scope with a "New assistant" modal and card action menu (Open, Settings, Delete).
   - `/settings` (or workspace settings): Tenant general settings (read-only tenant name) and members placeholder ("Not connected yet").
   - Still **no analytics dashboards** (no charts, topics, or sentiment tracking).

2. **Assistant Level (`/assistants/[id]/...`) with Sidebar Navigation**:
   - **Home**: Direct chat consultation with this assistant. "New chat" always starts an empty conversation (previous conversation turns never leak into a new session). Retains source chips, PDF.js side-panel viewer, status lines, feedback controls, and session attachment.
   - **Data sources**: The document knowledge base management screen (former Knowledge page restyled). Features source type cards ("Add files" for PDF/XLSX/CSV [operational], "Text snippet" [coming later], "Q&A" [coming later]), search, document status table, review drawer, and document retirement.
   - **Instructions**: Configuration of tone, audience, and scope with an immutable grounding constraint.
   - **Guardrails**: Built-in safety rules shown as locked and permanently active, plus configurable blocked topics and custom refusal message.
   - **Actions**: Placeholder card ("Coming soon. Actions that call other systems need a separate security review.").
   - **Conversations**: Past conversation transcript viewer with search, refresh, source citations, and feedback marks.
   - **Settings**: General assistant details (name, copyable assistant ID, description) and Danger zone (delete all conversations, delete assistant).

### Architectural Constraints & Invariants Enforced
1. **Instructions Boundary (Invariant 4)**: Custom instructions shape tone and conversational scope only. System prompt templates prepend the fixed immutable instruction: answers must come strictly and exclusively from retrieved client documents, or refuse. Custom instructions cannot disable citation requirements or force hallucinated answers.
2. **Locked Built-in Guardrails**:
   - Built-in guardrails are permanently locked ON in the UI and backend:
     - Answers only from documents or refuse (Invariant 4)
     - Every answer cites document and page (Invariant 5)
     - Never shows another client's data (Tenant Boundary & RLS)
     - Unreadable fields are never guessed (Invariant 6)
     - Documents uploaded in chat never enter the knowledge base (Invariant 7)
3. **Actions Security Barrier**:
   - Model tool-calling and external API actions remain completely disabled ("Coming soon"). Any future outbound action touching patient data requires a dedicated threat model, HIPAA business associate review, and explicit staff confirmation workflow.
4. **Database Scoping (Invariant 1)**:
   - The LLM never receives database access, SQL execution capability, or direct schema interaction. All retrieval occurs strictly through `retriever_user` role queries bounded by `SET LOCAL app.current_tenant_id`.

## Considered Options
1. **Option 1: Keep single Knowledge Page + TopBar tabs**: Limited scalability; multiple assistants clutter the top bar; no place for per-assistant instructions or guardrail configuration.
2. **Option 2: Chatbase clone with Analytics & Channels**: Violates privacy principles, bloats frontend, and introduces third-party telemetry anti-patterns.
3. **Option 3 (Chosen)**: Clean Workspace + Assistant Shell with operational screens (Home, Data sources, Instructions, Guardrails, Conversations, Settings), high-contrast monochrome design, locked guardrails, and no vanity analytics.

## Consequences & Trade-offs
- **Positive**: Clear visual architecture; predictable per-assistant navigation; explicit separation of live vs unbuilt capabilities.
- **Negative / Risks**: UI navigation expanded from 2 screens to 7 screens per assistant.
- **Mitigation**: Every unbuilt backend path renders an explicit "Not connected yet" badge naming the required API route. In live mode, zero mock data is rendered or requested.

## Compliance & Invariant Verification
- Verified by automated tests in `web/__tests__/shell.test.tsx` checking sidebar item ordering, locked guardrail rendering, immutable instructions notice, "New chat" reset isolation, and live/demo isolation.

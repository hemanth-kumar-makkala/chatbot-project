# Frontend Web Application (`web/`)

## What Belongs Here
- Next.js application codebase (App Router or Pages matching AEGIS frontend conventions).
- Chat interface, source chip citations, and document status dashboards.
- Embedded `pdf.js` viewer for cited page inspection.
- Client-side authentication token handling and Server-Sent Events (SSE) streaming consumer.

## What Must NEVER Go Here
- Business rules, retrieval orchestration, or evidence assessment logic.
- Direct database connections or SQL queries.
- Cloud provider secret keys or backend master credentials.
- Permanent storage of client documents or protected health information (PHI).

# Security & PHI Rules

Source of truth: `Client Knowledge Assistant Architecture and Build Plan.md` (Sections: "Security, HIPAA and guardrails" and "Logging and observability").

- Zero real PHI in development: Strictly no real client or patient data in local, dev, or staging environments. Synthetic and de-identified data only.
- Operational telemetry isolation: Operational logs carry request metadata, node names, counts, latency, and status codes only. Never log prompts, user queries, document contents, or model outputs.
- Minimal graph state: LangGraph checkpoints and graph state contain control fields and entity IDs only (`request_id`, `tenant_id`, `evidence_ids`, etc.). Question text and evidence text are loaded once per request into a request-scoped context object living outside the checkpointed graph state.
- Secrets hygiene: API keys and credentials live solely in `.env` (git-ignored) or cloud secret managers. Never commit or hardcode credentials.

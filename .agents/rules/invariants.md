# The 15 Invariants

Source of truth: `Client Knowledge Assistant Architecture and Build Plan.md` (Section: "The 15 invariants").

1. The model never has database credentials.
2. Tenant authorization is enforced before retrieval.
3. The model never decides access control.
4. Every knowledge answer has sufficient evidence or refuses.
5. Visual readings are derived evidence: always cited as visual, shown with the page image, never treated as equal to original text.
6. Unreadable fields get an explicit state, never a guess.
7. PHI uploaded in chat never enters the shared knowledge index unless explicitly promoted.
8. Every answer traces to a document version and page.
9. Component failure may degrade retrieval but never bypasses the evidence gate.
10. Operational telemetry never depends on PHI redaction working.
11. All ingestion steps are resumable and idempotent.
12. Model providers are replaceable behind interfaces.
13. Every failure is traceable to one node and one request.
14. No real PHI in local or staging environments.
15. Duplicate detection and caching never cross client boundaries.

# Architecture Rules

Source of truth: `Client Knowledge Assistant Architecture and Build Plan.md` (Sections: "Core principle: a fixed engine with swappable parts" and "Proposed architecture").

- Engine isolation: `backend/app/core/` depends strictly on interfaces in `backend/app/ports/`. Never import adapters or third-party vendor SDKs directly inside `core/`.
- Vendor encapsulation: Vendor libraries (Ollama, Vertex, Docling, Procrastinate, etc.) live strictly inside `backend/app/adapters/`.
- No environment branching: Never use `if production:` or environment-checking logic inside the engine. Swapping occurs via runtime configuration profiles.
- Tenant isolation: `tenant_id` must come strictly from authenticated session claims; never trust user inputs or model-generated parameters for tenancy.
- Database access boundary: The AI model and model adapters never receive database credentials or access. Only the `Retriever` port implementation queries chunk data.
- Normalized visual comparison: Two vision readings never match word for word; visual validation compares normalized values (digits, dates, IDs, amounts) rather than raw prose.
- Administrative scope: No generic admin dashboard; the Knowledge page and its review list are the only administrative screens.

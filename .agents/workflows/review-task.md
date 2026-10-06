# /review-task Workflow

Execute this workflow when completing an implementation task to verify compliance against the architecture spec.
Execution rule: All commands must follow `.agents/rules/execution.md` (strictly foreground, no background tasks).

Evaluate each criterion and report **PASS** or **FAIL** with evidence:

1. **Automated Tests**:
   - [ ] Unit, integration, and invariant tests pass completely (`pytest backend/tests/`).
   - [ ] No existing tests weakened or bypassed.
2. **Architecture & Boundaries**:
   - [ ] Zero vendor imports inside `backend/app/core/`.
   - [ ] All external dependencies isolated in `backend/app/adapters/` behind `backend/app/ports/`.
   - [ ] No `if production:` or environment-specific conditional branches in the engine.
3. **Multi-Tenancy & Access Control**:
   - [ ] `tenant_id` sourced strictly from authenticated session context.
   - [ ] Tenant filter present on every database query.
   - [ ] Postgres RLS policies verified on all tenant-scoped tables.
4. **Security & Data Sanitization**:
   - [ ] Zero PHI, query text, or document payloads in operational logs.
   - [ ] Graph state contains IDs and control fields only.
   - [ ] No secrets or keys committed in source files.
5. **Traceability & Configuration**:
   - [ ] Node execution records written to `run_steps`.
   - [ ] All library versions strictly pinned.
   - [ ] ADR drafted for any architectural or specification deviation.

# Client Knowledge Assistant (Antigravity Guide & Rules)

Staff-facing, per-client knowledge assistant to replace Chatbase, engineered on a fixed custom engine and deployed at $0 locally and on GCP under an existing BAA.
Retrieves trustworthy evidence, invokes vision models only when page images require it, and strictly limits AI generation to validated evidence within the authenticated client tenant scope.
Architecture specification: `Client Knowledge Assistant Architecture and Build Plan.md`.

## Mandatory Task Protocol
Before any task, read `docs/STATUS.md` and the current phase file (`docs/phases/PHASE-X.md`).
Mandatory: All commands must execute in the foreground according to `.agents/rules/execution.md` (no background execution).
Before reporting any task done: run uv run pre-commit run --files <changed files>. Never run git add, commit, or push. Hemanth does that.
After any task, update `docs/STATUS.md` and append an entry to `docs/BUILD_LOG.md`.

## Build Rules ("Working with Antigravity")
0. Never view, grep, or open .env files or any file containing credentials.
1. One module per task, with its section of the spec referenced.
2. Interfaces and tests are written and reviewed first. Implementation starts only when tests exist and fail.
3. Pin every library version. No new dependency without asking.
4. No vendor names inside `backend/app/core/`. Vendors live only in `backend/app/adapters/`.
5. Never weaken a test or an invariant to make a build pass. Report the conflict instead.
6. Every new node writes `run_steps` and operational logs in the agreed format.
7. All test data is synthetic. No real documents in the repository, ever.
8. Each module ends with a short review: what was built, what was decided, what is unclear. Decisions go into an ADR.
9. Stop and ask when the spec is silent. Do not fill gaps with assumptions.

## Review Checklist (Before Accepting Any Module)
- [ ] Staged all changes with `git add -A`, and `pre-commit run --all-files` passes on all files.
- [ ] Tests pass, including all invariant tests.
- [ ] No `if production:` branches in the engine.
- [ ] Tenant filter present on every query, RLS policy present on every tenant table.
- [ ] No content in operational logs (metadata and IDs only).
- [ ] Versions pinned, ADR written for any new decision.

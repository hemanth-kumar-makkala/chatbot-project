# /close-task Workflow

Execute this workflow upon passing the `/review-task` checklist to formally close out a task.

1. **Update `docs/STATUS.md`**:
   - Mark the completed task as finished.
   - Designate the current phase, next active task, and record any newly discovered blockers.
2. **Append to `docs/BUILD_LOG.md`**:
   - Add a new append-only entry formatted with:
     - Date (ISO 8601)
     - Task ID and title
     - What was built (modules, interfaces, adapters)
     - Automated tests added and verification status
     - Decisions made and rationale
     - Open issues or questions identified
3. **Record Architectural Decisions**:
   - If any design assumption, library selection, or deviation from the spec occurred, draft an ADR in `docs/decisions/` using `docs/decisions/ADR-0000-template.md`.
4. **Handoff**:
   - Report closing summary and indicate readiness for the subsequent task.

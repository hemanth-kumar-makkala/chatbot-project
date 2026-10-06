# /next-task Workflow

Execute this workflow when initiating a new work session or transitioning to the next task.
Execution rule: All commands must follow `.agents/rules/execution.md` (strictly foreground, no background tasks).

1. **Context Assessment**:
   - Read `docs/STATUS.md` to identify the active phase, current task, and any blockers.
   - Read the relevant phase roadmap file: `docs/phases/PHASE-X.md`.
2. **Task Framing**:
   - Restate the task scope, objectives, and acceptance criteria in 2-4 sentences.
   - Verify that all dependencies for this task are met.
3. **Test Planning (TDD Gate)**:
   - Enumerate the exact automated tests (unit, integration, invariant, or contract) that must be created first.
   - Specify the failure criteria that demonstrate the tests are failing before implementation.
4. **Approval Gate**:
   - Present the task summary and test plan to the user.
   - **WAIT for user approval before writing any implementation code.**

# Working Rule: No Background Execution

1. **Foreground Execution Only**:
   - Never run any command in the background. Run every command in the foreground, wait for it to finish, and report its full output.
   - Never say "running in the background" or "waiting for results". If you cannot run something to completion, stop and hand it to the user.

2. **Long-Running & Blocking Commands**:
   - Long-running or blocking commands (dev servers, `langgraph dev`, `docker compose up`, watchers, anything that does not exit on its own): do not start them. Give the user the exact command, the folder to run it from, and what output to expect. The user will run it in PowerShell and paste the result.

3. **Escalation**:
   - If anything fails for permissions, network, GUI, or anything you cannot do: stop, tell the user exactly what is needed, and the user will do it by hand.

4. **Shared State Guardrail**:
   - Ask the user before any command that changes shared state: migrations on the shared database, creating or dropping databases outside a test run, installing or removing packages, deleting files, git push.
   - Running the test suite is allowed without asking.

5. **Pre-Commit Verification Before Every Handoff**:
   - Run `git add -A` and `pre-commit run --all-files` before every handoff to the user, not only when a task is done.
   - Never hand over code that fails the hooks.

6. **Reporting Requirement**:
   - Every report ends with two lists: every command you ran (in order) and every file you created or edited.

7. **Process Management**:
   - The `manage_task` tool manages background tasks (listing, checking status, sending input, or cancelling running tasks). Do not use it to start or run processes.

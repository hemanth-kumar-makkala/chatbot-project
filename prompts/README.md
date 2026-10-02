# Prompt Registry (`prompts/`)

## What Belongs Here
- Centralized, versioned prompt definitions referenced across all model roles (`registry.yaml`).
- Template files or structured system prompt definitions.
- Every model invocation in the system must resolve through an explicit prompt ID and semver version (e.g. `answer-exact:1.0.0`).

## What Must NEVER Go Here
- Hardcoded client secrets, environment credentials, or client-specific tenant names.
- Ad-hoc, unversioned prompt strings constructed dynamically in application code.
- Real PHI examples or raw clinical fixtures.

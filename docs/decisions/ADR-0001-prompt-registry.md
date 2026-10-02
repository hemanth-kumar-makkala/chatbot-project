# ADR-0001: Centralized Versioned Prompt Registry

- **Status**: Accepted
- **Date**: 2026-10-02
- **Author**: Antigravity / Srinath Gollapally

## Context & Problem Statement
In multi-tenant AI systems handling healthcare information, prompt drifts and unversioned inline strings create major compliance, auditability, and regression debugging challenges. Section "Logging and observability" requires that every answer records the prompt version and model version that produced it. Without a centralized, immutable registry, tracking prompt changes across multiple model roles (`router`, `answer`, `vision`, `extract`, `judge`) is error-prone.

## Decision Drivers
- Invariant 13: Every failure is traceable to one node and one request.
- Observability requirements: Operational and trace logging must capture exact prompt versions.
- Guardrails: Strict structured JSON output schemas must be tied immutably to prompt templates.
- Modular engine: Prompt templates must not be scattered inside engine code or hardcoded in adapters.

## Considered Options
1. **Inline Strings in Engine Nodes**: Hardcode prompt templates inside LangGraph node functions.
   - *Downside*: Violates single responsibility, hinders auditability, prevents version tracking.
2. **Database-Backed Prompt Management**: Store prompts in PostgreSQL with dynamic UI editing.
   - *Downside*: Introduces mutable state, database dependency for engine compilation, and risk of untested production prompt changes.
3. **Central Versioned Prompt Registry File (`prompts/registry.yaml`)**:
   - Store all prompt templates, default versions, schemas, and role associations in a versioned YAML file in the repository root. Every model call references a prompt ID and semver version.

## Decision Outcome
Chosen option: **Option 3 (`prompts/registry.yaml`)**.

### Consequences & Trade-offs
- **Positive**:
  - Prompts are version-controlled via Git alongside code.
  - Every model execution records an unambiguous `prompt_id` and `prompt_version` in `run_steps`.
  - Structured output schemas are defined alongside prompt instructions.
  - Changes to prompts are subject to code review and automated regression testing against the golden dataset.
- **Negative / Risks**:
  - Updating a prompt requires a configuration edit and release commit rather than dynamic runtime dashboard tweaking. (This is a feature for regulated healthcare workflows, not a bug).

## Compliance & Invariant Verification
- Verified by `tests/unit/test_prompt_registry_resolver.py`: Asserts that any model call omitting a registered prompt ID and version fails at invocation time.
- Trace step records in `run_steps` must include valid `prompt_version` matching `prompts/registry.yaml`.

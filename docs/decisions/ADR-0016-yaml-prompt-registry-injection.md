# ADR-0016: YAML Prompt Registry Loading Outside Core via Dependency Injection

- **Status**: Accepted
- **Date**: 2026-10-21
- **Author**: Antigravity / Srinath Gollapally

## Context & Problem Statement
The architectural specification and ADR-0001 designate `prompts/registry.yaml` as the authoritative, version-controlled prompt registry. However, Clean Architecture rules dictate:
1. `app/core/` must remain pure Python (stdlib + Pydantic) and must never import vendor libraries.
2. Parsing YAML files in Python requires PyYAML (`yaml`), which is a third-party vendor dependency.
3. In a previous interim step, `prompts/registry.json` had been loaded directly inside `app/core/prompts.py` using Python's stdlib `json` parser to avoid importing PyYAML in core.
4. However, using `registry.json` diverges from the single source of truth (`prompts/registry.yaml`).

## Decision Drivers
- **Single Source of Truth**: All prompts, schemas, and versions must reside solely in `prompts/registry.yaml`.
- **Clean Architecture Invariant**: No vendor names or vendor dependencies (including `pyyaml`) inside `backend/app/core/`.
- **Explicit Boundary Enforcement**: Core receives domain inputs and configurations via injection or composition roots (`backend/app/wiring.py`).

## Decision Outcome
1. **Adapter Loader**: Created `backend/app/adapters/prompts/yaml_loader.py` containing `load_yaml_prompt_registry()`. This module safely imports `yaml` and parses `prompts/registry.yaml`.
2. **Core Injection**: `backend/app/core/prompts.py` exposes `set_prompt_registry(registry: dict[str, Any])` and `get_prompt_registry()`. Core no longer loads any files or uses `registry.json`. If `get_prompt_registry()` is called prior to injection, it raises a clear `RuntimeError`.
3. **Composition Root Injection**: `backend/app/wiring.py` (and test harness `conftest.py`) loads the YAML registry using the adapter and injects the parsed dictionary into core at startup.
4. **No Deletions**: Existing `prompts/registry.json` is preserved without deletion to honor the zero-delete policy, but is completely retired from use.

## Consequences & Verification
- Pure architecture boundaries preserved: `app/core` has 0 vendor imports.
- `prompts/registry.yaml` is the sole operative prompt registry.
- Verified by `backend/tests/unit/test_generate_node_schemas.py` and unit/integration test suites.

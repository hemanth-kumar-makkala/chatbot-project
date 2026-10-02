# ADR-0003: Single Composition Root for Dependency Injection (`app/wiring.py`)

- **Status**: Accepted
- **Date**: 2026-10-02
- **Author**: Antigravity / Srinath Gollapally

## Context & Problem Statement
In a hexagonal/ports-and-adapters architecture, the core business engine (`app/core/`) must remain entirely agnostic of concrete infrastructure and vendor SDKs (`app/adapters/`). If arbitrary modules (e.g. API route handlers, background tasks, or utilities) import concrete adapters and wire them into core components directly, coupling leaks across architectural layers, violating Invariants 1, 9, and 12. We must designate a single authoritative location where concrete adapters are instantiated and bound to port interfaces.

## Decision Drivers
- Rule 4: No vendor names inside `backend/app/core/`. Vendors live only in `backend/app/adapters/`.
- Invariant 1: The model never has database credentials.
- Invariant 12: Model providers are replaceable behind interfaces.
- Testability: The ability to run the exact same engine on `mock`, `office-cpu`, `desktop-gpu`, and `cloud` profiles solely via configuration.

## Considered Options
1. **Ad-hoc Wiring in API Routes (`app/api/`)**:
   - Each FastAPI endpoint or router instantiates the adapters it needs.
   - *Downside*: Causes circular dependencies, duplicates adapter configuration, and scatters profile switching logic across API routes.
2. **Dynamic In-Engine Dependency Resolution**:
   - Engine nodes import string class names dynamically using `importlib`.
   - *Downside*: Bypasses static type checking, introduces runtime import failures, and obscures architectural dependencies.
3. **Single Composition Root (`app/wiring.py`)**:
   - Exactly one dedicated module (`app/wiring.py`) is permitted to import both `app.core` and `app.adapters`. It reads the active runtime profile (e.g. `mock`, `desktop-gpu`, `cloud`) and injects concrete adapter instances into port abstractions.

## Decision Outcome
Chosen option: **Option 3 (`app/wiring.py`)**.

### Consequences & Trade-offs
- **Positive**:
  - `app/core/` and `app/ports/` remain 100% vendor-free and infrastructure-agnostic.
  - Runtime profile resolution is centralized in one file.
  - Adding a new vendor or model provider requires changing only its adapter and the composition module.
  - Automated AST tests can strictly prohibit any other file from importing core and adapters together.
- **Negative / Risks**:
  - `app/wiring.py` acts as a central hub; care must be taken to keep it purely declarative and free of domain logic.

## Compliance & Invariant Verification
- Verified by `tests/invariants/test_architecture_boundaries.py`:
  - `test_violation_unauthorized_module_imports_both_core_and_adapters()` asserts that any module other than `app/wiring.py` importing both `core` and `adapters` fails the build.
  - `test_repo_scan_only_wiring_is_composition_root()` scans all repository files and asserts zero unauthorized composition roots exist.

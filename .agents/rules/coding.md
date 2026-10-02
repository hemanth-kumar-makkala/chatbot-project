# Coding Standards

Source of truth: `Client Knowledge Assistant Architecture and Build Plan.md` (Sections: "Tech stack" and "Working with Antigravity").

- Language runtime: Python 3.12 exclusively for backend services.
- Strict dependency pinning: Every external package version must be pinned in requirements files. Never add new dependencies without explicit prior approval.
- Type integrity: Full type annotations (PEP 484) are mandatory for all function signatures and class definitions.
- Test-driven development (TDD): Interfaces and failing tests must be written and validated before beginning implementation code.
- Defensive error handling: Handle all component failure modes explicitly with safe fallbacks and bounded retries.

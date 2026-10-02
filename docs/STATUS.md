# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile (Ready to execute in parallel on the 8 GB office PC with mock profile)

## Current Task
- **TASK 0.3b**: .gitignore path scoping anchored to repo root and commit preparation — **COMPLETED**

## Next Task
- **Task 1.1**: Pre-commit quality checks and linting setup (Ruff, Mypy, Python 3.12 configuration) in `backend/`

## Blockers
- **None for Phase 1**: Phase 1 operates entirely on the `mock` profile with synthetic data on the 8 GB office PC (Docker memory capped at 3 GB).
- **External Governance Gates (Phase 6/7 Prerequisites)**:
  - Pharmacy system Gemini key migration to Vertex AI under GCP BAA.
  - Formal retention periods agreement with Dan and compliance.
  - Pilot client practice BAA execution before live data ingestion.

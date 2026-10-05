# Project Status

## Active Phase
- **Phase 0**: Foundations (Compliance route, retention rules, pilot workload, synthetic golden set, UI design spec; governance ongoing)
- **Phase 1**: Engine on Mock Profile (Active execution on 8 GB office PC with mock profile)

## Current Task
- **Task 1.7**: LangGraph Studio setup for local visual debugging (`langgraph.json`, dedicated entrypoint, zero telemetry egress) — **COMPLETED**

## Next Task
- **Task 1.8**: Fallback Table Failure-Mode Test Suite (or next Phase 1 task per build plan)




## Blockers
- **None for Phase 1**: Phase 1 operates entirely on the `mock` profile with synthetic data on the 8 GB office PC (Docker memory capped at 3 GB).
- **External Governance Gates (Phase 6/7 Prerequisites)**:
  - Pharmacy system Gemini key migration to Vertex AI under GCP BAA.
  - Formal retention periods agreement with Dan and compliance.
  - Pilot client practice BAA execution before live data ingestion.

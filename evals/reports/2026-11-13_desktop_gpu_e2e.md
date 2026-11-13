# Desktop-GPU Local End-to-End Evaluation Report (2026-11-13)

## Report Stamp
- **Timestamp**: `2026-11-13T18:19:36.658734+00:00`
- **Profile**: `desktop-gpu`
- **Git Commit**: `9791cda469f7f55b3a7fcb39a2c174059718ea89`
- **Machine**: `DESKTOP-ST369AG` (Windows-11-10.0.26300-SP0)
- **GPU**: `NVIDIA GeForce RTX 3050`
- **Models**:
  - Router: `ollama/qwen3:8b`
  - Answer: `ollama/qwen3:8b`
  - Vision / Extract / Judge: `ollama/qwen3-vl:4b-instruct`

## Overall Summary
- **Total Cases**: 12
- **Passed**: 11 (91.7%)
- **Failed**: 1
- **Unclear**: 0
- **Latency p50**: 14.51 s
- **Latency p95**: 30.99 s

## Results by Category
| Category | Total | Passed | Failed | Pass Rate |
| :--- | :--- | :--- | :--- | :--- |
| EXTRACTION | 1 | 1 | 0 | 100.0% |
| GENERAL | 2 | 2 | 0 | 100.0% |
| NOT_IN_DOCUMENTS | 2 | 2 | 0 | 100.0% |
| SESSION_DOCUMENT | 3 | 2 | 1 | 66.7% |
| TABLE | 2 | 2 | 0 | 100.0% |
| VERSION_CONFLICT | 2 | 2 | 0 | 100.0% |

## Failures Grouped by Fault Layer
| Layer at Fault | Count | Description |
| :--- | :--- | :--- |
| validation | 1 | Evaluated failures attributed to validation |

## Top Failures with Full Details
### 1. Case `E2E-SD-003` (SESSION_DOCUMENT)
- **Question**: "please summarize this fax"
- **Expected Route**: `session_document` | **Actual Route**: `session_document`
- **Expected Answer**: "Alex Morgan"
- **Actual Answer**: "I couldn't confirm that from the attached document."
- **Fault Layer**: `validation`
- **Failure Reason**: Unexpected refusal in session document: 'I couldn't confirm that from the attached document.'
- **Latency**: 16.3 s

## Complete Case Log
| ID | Category | Verdict | Route | Latency | Citations | Reason |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| E2E-SD-001 | SESSION_DOCUMENT | **PASS** | session_document | 24.22s | skewed_fax.pdf | Answered correctly with session document citation |
| E2E-SD-002 | SESSION_DOCUMENT | **PASS** | session_document | 3.91s | None | Correctly refused missing value in session document |
| E2E-SD-003 | SESSION_DOCUMENT | **FAIL** | session_document | 16.3s | None | Unexpected refusal in session document: 'I couldn't confirm that from the attached document.' |
| E2E-GEN-001 | GENERAL | **PASS** | general | 4.77s | None | General route answered cleanly with zero sources |
| E2E-GEN-002 | GENERAL | **PASS** | general | 6.29s | None | General route answered cleanly with zero sources |
| E2E-TAB-001 | TABLE | **PASS** | knowledge | 8.83s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Table query answered with proper spreadsheet citation |
| E2E-TAB-002 | TABLE | **PASS** | knowledge | 19.99s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Table query answered with proper spreadsheet citation |
| E2E-CONF-001 | VERSION_CONFLICT | **PASS** | knowledge | 12.73s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly detected with both v1 and v2 cited |
| E2E-CONF-002 | VERSION_CONFLICT | **PASS** | knowledge | 37.84s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly detected with both v1 and v2 cited |
| E2E-NOA-001 | NOT_IN_DOCUMENTS | **PASS** | knowledge | 25.39s | None | Correctly refused with zero citations |
| E2E-NOA-002 | NOT_IN_DOCUMENTS | **PASS** | knowledge | 3.39s | None | Correctly refused with zero citations |
| E2E-EXT-001 | EXTRACTION | **PASS** | None | 22.88s | None | All expected extraction fields matched |

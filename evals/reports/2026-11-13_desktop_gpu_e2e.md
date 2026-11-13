# Desktop-GPU Local End-to-End Evaluation Report (2026-11-13)

## Report Stamp
- **Timestamp**: `2026-11-13T17:56:46.907420+00:00`
- **Profile**: `desktop-gpu`
- **Git Commit**: `d18933f3250fa9bde006ff1ab66bd91b282f1be1`
- **Machine**: `DESKTOP-ST369AG` (Windows-11-10.0.26300-SP0)
- **GPU**: `NVIDIA GeForce RTX 3050`
- **Models**:
  - Router: `ollama/qwen3:8b`
  - Answer: `ollama/qwen3:8b`
  - Vision / Extract / Judge: `ollama/qwen3-vl:4b-instruct`

## Overall Summary
- **Total Cases**: 12
- **Passed**: 9 (75.0%)
- **Failed**: 3
- **Unclear**: 0
- **Latency p50**: 7.15 s
- **Latency p95**: 20.57 s

## Results by Category
| Category | Total | Passed | Failed | Pass Rate |
| :--- | :--- | :--- | :--- | :--- |
| EXTRACTION | 1 | 1 | 0 | 100.0% |
| GENERAL | 2 | 2 | 0 | 100.0% |
| NOT_IN_DOCUMENTS | 2 | 2 | 0 | 100.0% |
| SESSION_DOCUMENT | 3 | 1 | 2 | 33.3% |
| TABLE | 2 | 1 | 1 | 50.0% |
| VERSION_CONFLICT | 2 | 2 | 0 | 100.0% |

## Failures Grouped by Fault Layer
| Layer at Fault | Count | Description |
| :--- | :--- | :--- |
| routing | 1 | Evaluated failures attributed to routing |
| validation | 1 | Evaluated failures attributed to validation |
| generation | 1 | Evaluated failures attributed to generation |

## Top Failures with Full Details
### 1. Case `E2E-SD-001` (SESSION_DOCUMENT)
- **Question**: "what is the dosage in this fax?"
- **Expected Route**: `session_document` | **Actual Route**: `clarify`
- **Expected Answer**: "Take 1 capsule by mouth three times daily for 10 days"
- **Actual Answer**: "Could you please clarify which document or procedure you are referring to?"
- **Fault Layer**: `routing`
- **Failure Reason**: Route mismatch: expected 'session_document', got 'clarify'
- **Latency**: 2.18 s

### 2. Case `E2E-SD-003` (SESSION_DOCUMENT)
- **Question**: "please summarize this fax"
- **Expected Route**: `session_document` | **Actual Route**: `session_document`
- **Expected Answer**: "Alex Morgan"
- **Actual Answer**: "I couldn't confirm that from the attached document."
- **Fault Layer**: `validation`
- **Failure Reason**: Unexpected refusal in session document: 'I couldn't confirm that from the attached document.'
- **Latency**: 3.77 s

### 3. Case `E2E-TAB-001` (TABLE)
- **Question**: "what is the turnaround time for a routine prior authorization?"
- **Expected Route**: `knowledge` | **Actual Route**: `knowledge`
- **Expected Answer**: "Hours"
- **Actual Answer**: "[Sheet: Sheet1, Row: 6] *** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***: Summit Care Plan
[Sheet: Sheet1, Row: 4] *** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***: Beacon Mutual Healthcare
[Sheet: Sheet1, Row: 2] *** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***: Apex Health Insurance"
- **Fault Layer**: `generation`
- **Failure Reason**: Table answer missing expected term 'Hours'
- **Latency**: 7.77 s

## Complete Case Log
| ID | Category | Verdict | Route | Latency | Citations | Reason |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| E2E-SD-001 | SESSION_DOCUMENT | **FAIL** | clarify | 2.18s | None | Route mismatch: expected 'session_document', got 'clarify' |
| E2E-SD-002 | SESSION_DOCUMENT | **PASS** | session_document | 3.95s | None | Correctly refused missing value in session document |
| E2E-SD-003 | SESSION_DOCUMENT | **FAIL** | session_document | 3.77s | None | Unexpected refusal in session document: 'I couldn't confirm that from the attached document.' |
| E2E-GEN-001 | GENERAL | **PASS** | general | 2.75s | None | General route answered cleanly with zero sources |
| E2E-GEN-002 | GENERAL | **PASS** | general | 6.52s | None | General route answered cleanly with zero sources |
| E2E-TAB-001 | TABLE | **FAIL** | knowledge | 7.77s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Table answer missing expected term 'Hours' |
| E2E-TAB-002 | TABLE | **PASS** | knowledge | 20.89s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Table query answered with proper spreadsheet citation |
| E2E-CONF-001 | VERSION_CONFLICT | **PASS** | knowledge | 12.51s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly detected with both v1 and v2 cited |
| E2E-CONF-002 | VERSION_CONFLICT | **PASS** | knowledge | 16.41s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly detected with both v1 and v2 cited |
| E2E-NOA-001 | NOT_IN_DOCUMENTS | **PASS** | knowledge | 9.3s | None | Correctly refused with zero citations |
| E2E-NOA-002 | NOT_IN_DOCUMENTS | **PASS** | knowledge | 2.87s | None | Correctly refused with zero citations |
| E2E-EXT-001 | EXTRACTION | **PASS** | None | 20.31s | None | All expected extraction fields matched |

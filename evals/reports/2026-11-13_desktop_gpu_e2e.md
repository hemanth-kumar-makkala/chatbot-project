# Desktop-GPU Local End-to-End Evaluation Report (2026-11-13)

## Report Stamp
- **Timestamp**: `2026-11-13T15:04:07.126769+00:00`
- **Profile**: `desktop-gpu`
- **Git Commit**: `69a26591fd11739f173d1b913a3a78d37d31f28c`
- **Machine**: `DESKTOP-ST369AG` (Windows-11-10.0.26300-SP0)
- **GPU**: `NVIDIA GeForce RTX 3050`
- **Models**:
  - Router: `ollama/qwen3:8b`
  - Answer: `ollama/qwen3:8b`
  - Vision / Extract / Judge: `ollama/qwen3-vl:4b-instruct`

## Overall Summary
- **Total Cases**: 12
- **Passed**: 7 (58.3%)
- **Failed**: 5
- **Unclear**: 0
- **Latency p50**: 19.78 s
- **Latency p95**: 38.68 s

## Results by Category
| Category | Total | Passed | Failed | Pass Rate |
| :--- | :--- | :--- | :--- | :--- |
| EXTRACTION | 1 | 0 | 1 | 0.0% |
| GENERAL | 2 | 2 | 0 | 100.0% |
| NOT_IN_DOCUMENTS | 2 | 2 | 0 | 100.0% |
| SESSION_DOCUMENT | 3 | 0 | 3 | 0.0% |
| TABLE | 2 | 1 | 1 | 50.0% |
| VERSION_CONFLICT | 2 | 2 | 0 | 100.0% |

## Failures Grouped by Fault Layer
| Layer at Fault | Count | Description |
| :--- | :--- | :--- |
| routing | 3 | Evaluated failures attributed to routing |
| generation | 1 | Evaluated failures attributed to generation |
| extraction | 1 | Evaluated failures attributed to extraction |

## Top Failures with Full Details
### 1. Case `E2E-SD-001` (SESSION_DOCUMENT)
- **Question**: "what is the dosage in this fax?"
- **Expected Route**: `session_document` | **Actual Route**: `clarify`
- **Expected Answer**: "Take 1 capsule by mouth three times daily for 10 days"
- **Actual Answer**: "Could you please clarify which document or procedure you are referring to?"
- **Fault Layer**: `routing`
- **Failure Reason**: Route mismatch: expected 'session_document', got 'clarify'
- **Latency**: 1.81 s

### 2. Case `E2E-SD-002` (SESSION_DOCUMENT)
- **Question**: "what is the patient's date of birth in this fax?"
- **Expected Route**: `session_document` | **Actual Route**: `clarify`
- **Expected Answer**: "I couldn't confirm that from the attached document."
- **Actual Answer**: "Could you please clarify which document or procedure you are referring to?"
- **Fault Layer**: `routing`
- **Failure Reason**: Route mismatch: expected 'session_document', got 'clarify'
- **Latency**: 1.43 s

### 3. Case `E2E-SD-003` (SESSION_DOCUMENT)
- **Question**: "please summarize this fax"
- **Expected Route**: `session_document` | **Actual Route**: `clarify`
- **Expected Answer**: "Alex Morgan"
- **Actual Answer**: "Could you please clarify which document or procedure you are referring to?"
- **Fault Layer**: `routing`
- **Failure Reason**: Route mismatch: expected 'session_document', got 'clarify'
- **Latency**: 1.46 s

### 4. Case `E2E-TAB-001` (TABLE)
- **Question**: "what is the turnaround time for a routine prior authorization?"
- **Expected Route**: `knowledge` | **Actual Route**: `knowledge`
- **Expected Answer**: "Hours"
- **Actual Answer**: "[Sheet: Sheet1, Row: 6] *** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***: Summit Care Plan
[Sheet: Sheet1, Row: 4] *** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***: Beacon Mutual Healthcare
[Sheet: Sheet1, Row: 2] *** SYNTHETIC TEST DOCUMENT - PRIOR AUTHORIZATION TURNAROUND TIMES ***: Apex Health Insurance"
- **Fault Layer**: `generation`
- **Failure Reason**: Table answer missing expected term 'Hours'
- **Latency**: 33.23 s

### 5. Case `E2E-EXT-001` (EXTRACTION)
- **Question**: "Extract fields from attached session document"
- **Expected Route**: `extraction` | **Actual Route**: `None`
- **Expected Answer**: "Alex Morgan"
- **Actual Answer**: "None"
- **Fault Layer**: `extraction`
- **Failure Reason**: Field 'authorized_medication' value mismatch: expected 'Amoxicillin 500mg', got 'None'
- **Latency**: 25.4 s

## Complete Case Log
| ID | Category | Verdict | Route | Latency | Citations | Reason |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| E2E-SD-001 | SESSION_DOCUMENT | **FAIL** | clarify | 1.81s | None | Route mismatch: expected 'session_document', got 'clarify' |
| E2E-SD-002 | SESSION_DOCUMENT | **FAIL** | clarify | 1.43s | None | Route mismatch: expected 'session_document', got 'clarify' |
| E2E-SD-003 | SESSION_DOCUMENT | **FAIL** | clarify | 1.46s | None | Route mismatch: expected 'session_document', got 'clarify' |
| E2E-GEN-001 | GENERAL | **PASS** | general | 1.97s | None | General route answered cleanly with zero sources |
| E2E-GEN-002 | GENERAL | **PASS** | general | 5.83s | None | General route answered cleanly with zero sources |
| E2E-TAB-001 | TABLE | **FAIL** | knowledge | 33.23s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Table answer missing expected term 'Hours' |
| E2E-TAB-002 | TABLE | **PASS** | knowledge | 45.36s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Table query answered with proper spreadsheet citation |
| E2E-CONF-001 | VERSION_CONFLICT | **PASS** | knowledge | 26.33s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly detected with both v1 and v2 cited |
| E2E-CONF-002 | VERSION_CONFLICT | **PASS** | knowledge | 30.55s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly detected with both v1 and v2 cited |
| E2E-NOA-001 | NOT_IN_DOCUMENTS | **PASS** | knowledge | 22.58s | None | Correctly refused with zero citations |
| E2E-NOA-002 | NOT_IN_DOCUMENTS | **PASS** | knowledge | 16.99s | None | Correctly refused with zero citations |
| E2E-EXT-001 | EXTRACTION | **FAIL** | None | 25.4s | None | Field 'authorized_medication' value mismatch: expected 'Amoxicillin 500mg', got 'None' |

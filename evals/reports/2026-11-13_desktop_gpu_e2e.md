# Desktop-GPU Local End-to-End Evaluation Report (2026-11-13)

## Report Stamp
- **Timestamp**: `2026-11-13T19:26:24.301196+00:00`
- **Profile**: `desktop-gpu`
- **Git Commit**: `533129af33788504901f44ac836f321e9942bfe3`
- **Machine**: `DESKTOP-ST369AG` (Windows-11-10.0.26300-SP0)
- **GPU**: `NVIDIA GeForce RTX 3050`
- **Models**:
  - Router: `ollama/qwen3:8b`
  - Answer: `ollama/qwen3:8b`
  - Vision / Extract / Judge: `ollama/qwen3-vl:4b-instruct`

## Overall Summary
- **Total Cases**: 67
- **Passed**: 50 (74.6%)
- **Failed**: 17
- **Unclear**: 0
- **Latency p50**: 6.85 s
- **Latency p95**: 26.83 s

## Results by Category
| Category | Total | Passed | Failed | Pass Rate |
| :--- | :--- | :--- | :--- | :--- |
| CONFLICT | 8 | 7 | 1 | 87.5% |
| CROSS_TENANT | 10 | 10 | 0 | 100.0% |
| EXTRACTION | 1 | 1 | 0 | 100.0% |
| GENERAL | 2 | 2 | 0 | 100.0% |
| NOT_IN_DOCUMENTS | 2 | 2 | 0 | 100.0% |
| NO_ANSWER | 16 | 13 | 3 | 81.2% |
| SESSION_DOCUMENT | 3 | 2 | 1 | 66.7% |
| TABLE | 2 | 1 | 1 | 50.0% |
| TABLE_LOOKUP | 6 | 3 | 3 | 50.0% |
| TEXT_FACT | 10 | 4 | 6 | 40.0% |
| VERSION_CONFLICT | 2 | 2 | 0 | 100.0% |
| VISUAL_RECOVERY | 5 | 3 | 2 | 60.0% |

## Failures Grouped by Fault Layer
| Layer at Fault | Count | Description |
| :--- | :--- | :--- |
| retrieval | 11 | Evaluated failures attributed to retrieval |
| generation | 4 | Evaluated failures attributed to generation |
| routing | 2 | Evaluated failures attributed to routing |

## Top Failures with Full Details
### 1. Case `E2E-SD-003` (SESSION_DOCUMENT)
- **Question**: "please summarize this fax"
- **Expected Route**: `session_document` | **Actual Route**: `session_document`
- **Expected Answer**: "Alex Morgan; Amoxicillin"
- **Actual Answer**: "The fax is a synthetic test document indicating a prescription refill authorization for Amoxicillin 500mg prescribed by Dr. Evelyn Reed for patient Alex Morgan (Chart #MRN-4412-A). The refill is approved for a one-time course of 10 days, with instructions to take 1 capsule by mouth three times daily. The transmission is marked as confidential and relates to pediatric and adult urgent care, with the clinic phone number 555-0145."
- **Fault Layer**: `generation`
- **Failure Reason**: E2E-SD-003 expected at least 2 bullet points, got 0
- **Latency**: 12.41 s

### 2. Case `E2E-TAB-001` (TABLE)
- **Question**: "what is the turnaround time for a routine prior authorization?"
- **Expected Route**: `knowledge` | **Actual Route**: `knowledge`
- **Expected Answer**: "48 to 72 hours"
- **Actual Answer**: "[Sheet: Sheet1, Row: 6] Standard Clinical Review, Routine, 72 Hours, 555-0123, Summit Care Plan"
- **Fault Layer**: `generation`
- **Failure Reason**: E2E-TAB-001 missing 48 or 72 in answer: '[Sheet: Sheet1, Row: 6] Standard Clinical Review, Routine, 72 Hours, 555-0123, Summit Care Plan'
- **Latency**: 5.72 s

### 3. Case `TF-001` (TEXT_FACT)
- **Question**: "What is the policy reference code for outpatient prior authorization?"
- **Expected Route**: `knowledge` | **Actual Route**: `knowledge`
- **Expected Answer**: "2026-PA-01"
- **Actual Answer**: "I couldn't confirm that from the documents."
- **Fault Layer**: `retrieval`
- **Failure Reason**: Unexpected refusal: 'I couldn't confirm that from the documents....'
- **Latency**: 16.47 s

### 4. Case `TF-003` (TEXT_FACT)
- **Question**: "How many business days in advance must outpatient surgery prior auths be submitted?"
- **Expected Route**: `knowledge` | **Actual Route**: `knowledge`
- **Expected Answer**: "5 business days"
- **Actual Answer**: "There is no 'many' category (many is not listed); Inpatient Surgery requests are 24 hours.
[Sheet: Sheet1, Row: 2] Urgency: Expedited, Payer Name: Apex Health Insurance, Standard SLA: 24 Hours, Liaison Contact: 555-0120, Service Category: Inpatient Surgery
[Sheet: Sheet1, Row: 3] Urgency: Routine, Payer Name: Beacon Mutual Healthcare, Standard SLA: 72 Hours, Liaison Contact: 555-0121, Service Category: Outpatient Therapy"
- **Fault Layer**: `retrieval`
- **Failure Reason**: Expected citation 'prior_auth_guide.pdf' missing from sources: ['pa_turnaround_times.xlsx', 'pa_turnaround_times.xlsx']
- **Latency**: 20.37 s

### 5. Case `TF-006` (TEXT_FACT)
- **Question**: "Within what timeframe must an urgent authorization appeal be filed after a denial?"
- **Expected Route**: `knowledge` | **Actual Route**: `knowledge`
- **Expected Answer**: "72 hours"
- **Actual Answer**: "There is no 'within' category in the table (within is not listed).
[Sheet: Sheet1, Row: 6] Standard Clinical Review, Routine, 72 Hours, 555-0123, Summit Care Plan"
- **Fault Layer**: `retrieval`
- **Failure Reason**: Expected citation 'prior_auth_guide.pdf' missing from sources: ['pa_turnaround_times.xlsx']
- **Latency**: 16.35 s

## Complete Case Log
| ID | Category | Verdict | Route | Latency | Citations | Reason |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| E2E-SD-001 | SESSION_DOCUMENT | **PASS** | session_document | 19.5s | skewed_fax.pdf | Session document answered correctly with citation |
| E2E-SD-002 | SESSION_DOCUMENT | **PASS** | session_document | 3.75s | None | Correctly refused with zero citations |
| E2E-SD-003 | SESSION_DOCUMENT | **FAIL** | session_document | 12.41s | skewed_fax.pdf | E2E-SD-003 expected at least 2 bullet points, got 0 |
| E2E-GEN-001 | GENERAL | **PASS** | general | 3.06s | None | General route answered cleanly with zero sources |
| E2E-GEN-002 | GENERAL | **PASS** | general | 7.88s | None | General route answered cleanly with zero sources |
| E2E-TAB-001 | TABLE | **FAIL** | knowledge | 5.72s | pa_turnaround_times.xlsx | E2E-TAB-001 missing 48 or 72 in answer: '[Sheet: Sheet1, Row: 6] Standard Clinical Review, Routine, 72 Hours, 555-0123, Summit Care Plan' |
| E2E-TAB-002 | TABLE | **PASS** | knowledge | 21.64s | pa_turnaround_times.xlsx | Answer matched expected content and citations strictly |
| E2E-CONF-001 | VERSION_CONFLICT | **PASS** | knowledge | 14.41s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly detected: 555-0102 present and both v1/v2 cited |
| E2E-CONF-002 | VERSION_CONFLICT | **PASS** | knowledge | 22.2s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly detected: 20 minutes present and both v1/v2 cited |
| E2E-NOA-001 | NOT_IN_DOCUMENTS | **PASS** | knowledge | 12.47s | None | Correctly refused with zero citations |
| E2E-NOA-002 | NOT_IN_DOCUMENTS | **PASS** | knowledge | 2.84s | None | Correctly refused with zero citations |
| E2E-EXT-001 | EXTRACTION | **PASS** | None | 22.73s | None | All expected extraction fields matched |
| TF-001 | TEXT_FACT | **FAIL** | knowledge | 16.47s | None | Unexpected refusal: 'I couldn't confirm that from the documents....' |
| TF-002 | TEXT_FACT | **PASS** | knowledge | 11.07s | prior_auth_guide.pdf | Answer matched expected content and citations strictly |
| TF-003 | TEXT_FACT | **FAIL** | knowledge | 20.37s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Expected citation 'prior_auth_guide.pdf' missing from sources: ['pa_turnaround_times.xlsx', 'pa_turnaround_times.xlsx'] |
| TF-004 | TEXT_FACT | **PASS** | knowledge | 27.18s | prior_auth_guide.pdf | Answer matched expected content and citations strictly |
| TF-005 | TEXT_FACT | **PASS** | knowledge | 9.54s | prior_auth_guide.pdf | Answer matched expected content and citations strictly |
| TF-006 | TEXT_FACT | **FAIL** | knowledge | 16.35s | pa_turnaround_times.xlsx | Expected citation 'prior_auth_guide.pdf' missing from sources: ['pa_turnaround_times.xlsx'] |
| TF-007 | TEXT_FACT | **FAIL** | knowledge | 27.29s | None | Unexpected refusal: 'I couldn't confirm that from the documents....' |
| TF-008 | TEXT_FACT | **FAIL** | knowledge | 63.95s | pa_turnaround_times.xlsx | Expected citation 'prior_auth_guide.pdf' missing from sources: ['pa_turnaround_times.xlsx'] |
| TF-009 | TEXT_FACT | **FAIL** | knowledge | 28.39s | None | Unexpected refusal: 'I couldn't confirm that from the documents....' |
| TF-010 | TEXT_FACT | **PASS** | knowledge | 26.02s | prior_auth_guide.pdf | Answer matched expected content and citations strictly |
| TL-011 | TABLE_LOOKUP | **FAIL** | knowledge | 13.25s | None | Unexpected refusal: 'I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times....' |
| TL-012 | TABLE_LOOKUP | **PASS** | knowledge | 7.58s | accepted_plans.csv | Answer matched expected content and citations strictly |
| TL-013 | TABLE_LOOKUP | **FAIL** | knowledge | 3.19s | None | Unexpected refusal: 'I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times....' |
| TL-014 | TABLE_LOOKUP | **FAIL** | knowledge | 3.15s | None | Unexpected refusal: 'I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times....' |
| TL-015 | TABLE_LOOKUP | **PASS** | knowledge | 7.74s | pa_turnaround_times.xlsx | Answer matched expected content and citations strictly |
| TL-016 | TABLE_LOOKUP | **PASS** | knowledge | 7.76s | pa_turnaround_times.xlsx | Answer matched expected content and citations strictly |
| VR-007 | VISUAL_RECOVERY | **PASS** | knowledge | 9.09s | skewed_fax.pdf | Answer matched expected content and citations strictly |
| VR-008 | VISUAL_RECOVERY | **PASS** | knowledge | 6.85s | skewed_fax.pdf | Answer matched expected content and citations strictly |
| VR-009 | VISUAL_RECOVERY | **FAIL** | knowledge | 4.35s | None | Unexpected refusal: 'I couldn't confirm that from the documents....' |
| VR-010 | VISUAL_RECOVERY | **PASS** | knowledge | 7.69s | skewed_fax.pdf | Answer matched expected content and citations strictly |
| VR-011 | VISUAL_RECOVERY | **FAIL** | knowledge | 6.44s | None | Unexpected refusal: 'I couldn't confirm that from the documents....' |
| CONF-001 | CONFLICT | **PASS** | knowledge | 10.22s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly answered with all citations and values |
| CONF-002 | CONFLICT | **PASS** | knowledge | 14.63s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly answered with all citations and values |
| CONF-003 | CONFLICT | **PASS** | knowledge | 14.38s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly answered with all citations and values |
| CONF-004 | CONFLICT | **PASS** | knowledge | 14.45s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly answered with all citations and values |
| CONF-005 | CONFLICT | **PASS** | knowledge | 14.2s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly answered with all citations and values |
| CONF-006 | CONFLICT | **PASS** | knowledge | 14.16s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly answered with all citations and values |
| CONF-007 | CONFLICT | **FAIL** | clarify | 6.56s | None | Route mismatch: expected 'knowledge', got 'clarify' |
| CONF-008 | CONFLICT | **PASS** | knowledge | 10.38s | front_desk_policy_v2.pdf, front_desk_policy_v1.pdf | Version conflict correctly answered with all citations and values |
| NOA-001 | NO_ANSWER | **PASS** | knowledge | 7.88s | None | Correctly refused with zero citations |
| NOA-002 | NO_ANSWER | **PASS** | knowledge | 2.85s | None | Correctly refused with zero citations |
| NOA-003 | NO_ANSWER | **PASS** | knowledge | 2.96s | None | Correctly refused with zero citations |
| NOA-004 | NO_ANSWER | **PASS** | knowledge | 2.79s | None | Correctly refused with zero citations |
| NOA-005 | NO_ANSWER | **PASS** | knowledge | 2.8s | None | Correctly refused with zero citations |
| NOA-006 | NO_ANSWER | **PASS** | knowledge | 2.83s | None | Correctly refused with zero citations |
| NOA-007 | NO_ANSWER | **PASS** | knowledge | 2.95s | None | Correctly refused with zero citations |
| NOA-008 | NO_ANSWER | **FAIL** | clarify | 2.45s | None | Route mismatch: expected 'knowledge', got 'clarify' |
| NOA-009 | NO_ANSWER | **PASS** | knowledge | 2.76s | None | Correctly refused with zero citations |
| NOA-010 | NO_ANSWER | **PASS** | knowledge | 2.07s | None | Correctly refused with zero citations |
| NOA-011 | NO_ANSWER | **PASS** | knowledge | 2.98s | None | Correctly refused with zero citations |
| NOA-012 | NO_ANSWER | **FAIL** | knowledge | 5.87s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Expected refusal, but got answered: 'There is no 'reimbursement' category (reimbursement is not listed); Inpatient Su...' |
| NOA-013 | NO_ANSWER | **FAIL** | knowledge | 5.63s | pa_turnaround_times.xlsx, pa_turnaround_times.xlsx | Expected refusal, but got answered: 'There is no 'processing' category (processing is not listed); Inpatient Surgery ...' |
| NOA-014 | NO_ANSWER | **PASS** | knowledge | 2.74s | None | Correctly refused with zero citations |
| NOA-015 | NO_ANSWER | **PASS** | knowledge | 2.88s | None | Correctly refused with zero citations |
| NOA-016 | NO_ANSWER | **PASS** | knowledge | 2.81s | None | Correctly refused with zero citations |
| XT-001 | CROSS_TENANT | **PASS** | knowledge | 2.86s | None | Correctly refused with zero citations |
| XT-002 | CROSS_TENANT | **PASS** | knowledge | 4.51s | None | Correctly refused with zero citations |
| XT-003 | CROSS_TENANT | **PASS** | knowledge | 4.08s | None | Correctly refused with zero citations |
| XT-004 | CROSS_TENANT | **PASS** | knowledge | 2.74s | None | Correctly refused with zero citations |
| XT-005 | CROSS_TENANT | **PASS** | knowledge | 2.09s | None | Correctly refused with zero citations |
| XT-006 | CROSS_TENANT | **PASS** | knowledge | 2.95s | None | Correctly refused with zero citations |
| XT-007 | CROSS_TENANT | **PASS** | knowledge | 11.32s | None | Correctly refused with zero citations |
| XT-008 | CROSS_TENANT | **PASS** | knowledge | 5.11s | None | Correctly refused with zero citations |
| XT-009 | CROSS_TENANT | **PASS** | knowledge | 5.27s | None | Correctly refused with zero citations |
| XT-010 | CROSS_TENANT | **PASS** | knowledge | 4.05s | None | Correctly refused with zero citations |

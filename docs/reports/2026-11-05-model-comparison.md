# Model Comparison Report — November 5, 2026

## 1. Executive Summary

This report provides an authoritative side-by-side evaluation of local, cloud, and hosted models across both answer-generation and multimodal vision extraction roles under identical test harnesses and throwaway PostgreSQL databases.

- **Answer Models Evaluated**:
  1. `qwen3-vl:4b` (Local 4B multimodal model)
  2. `beam:cloud` (Ollama Cloud hosted model)
  3. `nvidia:mistralai/mistral-large-4-1t-a52b` (NVIDIA Cloud OpenAI-compatible hosted model)
- **Vision Models Evaluated**:
  1. `qwen3-vl:4b` (Local 4B vision)
  2. `nvidia:mistralai/mistral-large-4-1t-a52b` (NVIDIA Cloud vision)
- **Safety Lock**: Enforced SHA-256 validation verifying that all active documents come from `evals/golden/docs/`.
- **Rate Limiting**: NVIDIA requests were sequenced one question at a time with a 1.5s delay (respecting the <= 40 RPM quota).

## 2. Per-Model Summary

| Model | Evaluated | Correct | Accuracy (%) | Refusals | Invalid JSON | Avg Latency (s) |
|---|---:|---:|---:|---:|---:|---:|
| `qwen3-vl:4b` | 15 | 13 | 86.67% | 3 | 0 | 14.51s |
| `beam:cloud` | 15 | 14 | 93.33% | 3 | 0 | 19.14s |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | 15 | 7 | 46.67% | 10 | 0 | 202.65s |

## 3. Answer Comparison Grid

| # | Question | qwen3-vl:4b (Correct / Time) | beam:cloud (Correct / Time) | NVIDIA mistral-large (Correct / Time) | Category |
|---|---|:---:|:---:|:---:|:---:|
| Q1 | HORIZON VALLEY HEALTH CLINIC data please | No (21.95s) | No (14.98s) | No (43.44s) | **ALL-FAIL** |
| Q2 | What is the front desk intake fax number? | Yes (13.89s) | Yes (16.48s) | No (315.99s) | **SOME-FAIL** |
| Q3 | How early must patients arrive for a routine visit? | Yes (13.47s) | Yes (18.88s) | No (190.58s) | **SOME-FAIL** |
| Q4 | Summarize the outpatient prior authorization guide | No (22.62s) | Yes (34.08s) | No (303.73s) | **SOME-FAIL** |
| Q5 | What insurance plans are accepted? | Yes (22.33s) | Yes (25.86s) | Yes (164.94s) | **ALL-PASS** |
| Q6 | What is the effective date for our outpatient prior authorization policy? | Yes (10.53s) | Yes (14.66s) | No (230.03s) | **SOME-FAIL** |
| Q7 | How many business days in advance must outpatient surgery prior auths be submitted? | Yes (12.78s) | Yes (19.62s) | Yes (183.42s) | **ALL-PASS** |
| Q8 | How recent must clinical consultation notes be when submitting an auth request? | Yes (10.41s) | Yes (17.38s) | Yes (113.86s) | **ALL-PASS** |
| Q9 | What is the policy reference code for outpatient prior authorization? | Yes (11.05s) | Yes (17.31s) | No (159.67s) | **SOME-FAIL** |
| Q10 | What is the copayment amount for acupuncture therapy sessions at Horizon Valley? | Yes (10.05s) | Yes (14.7s) | Yes (53.11s) | **ALL-PASS** |
| Q11 | What is the discounted parking rate for patients in the medical plaza garage? | Yes (9.7s) | Yes (11.08s) | Yes (41.92s) | **ALL-PASS** |
| Q12 | What is the standard SLA for Apex Health Insurance diagnostic imaging? | Yes (17.77s) | Yes (23.11s) | Yes (224.14s) | **ALL-PASS** |
| Chain-T1 | How many business days in advance must outpatient surgery prior auths be submitted? | Yes (12.88s) | Yes (16.88s) | No (278.55s) | **SOME-FAIL** |
| Chain-T2 | and what documents are required with it? | Yes (13.12s) | Yes (19.8s) | No (320.38s) | **SOME-FAIL** |
| Chain-T3 | how recent must they be? | Yes (15.05s) | Yes (22.28s) | Yes (415.92s) | **ALL-PASS** |

## 4. Failure Breakdown & Traces

### 4.1 ALL-FAIL (All Models Failed)

Total ALL-FAIL Questions: **1**

#### `Q1`: HORIZON VALLEY HEALTH CLINIC data please
- **Classification**: PIPELINE failure (deterministic guard or threshold failure common to all models).
- **qwen3-vl:4b Trace**: State `WEAK`, Sources: `[]`
  *Answer*: `I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times.xlsx
- accepted_plan...`
- **beam:cloud Trace**: State `WEAK`, Sources: `[]`
  *Answer*: `I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times.xlsx
- accepted_plan...`
- **NVIDIA mistral Trace**: State `WEAK`, Sources: `[]`
  *Answer*: `I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times.xlsx
- accepted_plan...`
- **Root Cause Analysis**:
  Underspecified query without concrete entity details scores below `RELIABLE_TEXT_THRESHOLD = 0.25` on hybrid retrieval, cleanly falling back to inventory.

### 4.2 SOME-FAIL (Discrepant Failures)

Total SOME-FAIL Questions: **7**

#### `Q2`: What is the front desk intake fax number?
- **4B**: PASS (13.89s) | **beam**: PASS (16.48s) | **NVIDIA**: FAIL (315.99s)
- **4B Answer**: `Front desk intake fax number is 555-0102.
Note: An older version (front_desk_policy_v1.pdf) stated 555-0101, while the n...`
- **Beam Answer**: `Front desk intake fax number is 555-0102.
Note: An older version (front_desk_policy_v1.pdf) stated 555-0101, while the n...`
- **NVIDIA Answer**: `I couldn't confirm that from the documents....`

#### `Q3`: How early must patients arrive for a routine visit?
- **4B**: PASS (13.47s) | **beam**: PASS (18.88s) | **NVIDIA**: FAIL (190.58s)
- **4B Answer**: `Routine appointment arrival window is 20 minutes prior to scheduled visit.
Note: An older version (front_desk_policy_v1....`
- **Beam Answer**: `Routine appointment arrival window is 20 minutes prior to scheduled visit.
Note: An older version (front_desk_policy_v1....`
- **NVIDIA Answer**: `I couldn't confirm that from the documents....`

#### `Q4`: Summarize the outpatient prior authorization guide
- **4B**: FAIL (22.62s) | **beam**: PASS (34.08s) | **NVIDIA**: FAIL (303.73s)
- **4B Answer**: `- The outpatient prior authorization submission guide requires providers to submit requests through the provider portal...`
- **Beam Answer**: `- All participating providers must submit prior authorization requests through the provider portal or via secure electro...`
- **NVIDIA Answer**: `I couldn't confirm that from the documents....`

#### `Q6`: What is the effective date for our outpatient prior authorization policy?
- **4B**: PASS (10.53s) | **beam**: PASS (14.66s) | **NVIDIA**: FAIL (230.03s)
- **4B Answer**: `January 1, 2026...`
- **Beam Answer**: `January 1, 2026...`
- **NVIDIA Answer**: `I couldn't confirm that from the documents....`

#### `Q9`: What is the policy reference code for outpatient prior authorization?
- **4B**: PASS (11.05s) | **beam**: PASS (17.31s) | **NVIDIA**: FAIL (159.67s)
- **4B Answer**: `Policy 2026-PA-01...`
- **Beam Answer**: `2026-PA-01...`
- **NVIDIA Answer**: `I couldn't confirm that from the documents....`

#### `Chain-T1`: How many business days in advance must outpatient surgery prior auths be submitted?
- **4B**: PASS (12.88s) | **beam**: PASS (16.88s) | **NVIDIA**: FAIL (278.55s)
- **4B Answer**: `5...`
- **Beam Answer**: `5...`
- **NVIDIA Answer**: `I couldn't confirm that from the documents....`

#### `Chain-T2`: and what documents are required with it?
- **4B**: PASS (13.12s) | **beam**: PASS (19.8s) | **NVIDIA**: FAIL (320.38s)
- **4B Answer**: `Complete clinical consultation notes within past 30 days, diagnostic imaging reports and relevant laboratory panels, pro...`
- **Beam Answer**: `Complete clinical consultation notes within the past 30 days; diagnostic imaging reports and relevant laboratory panels;...`
- **NVIDIA Answer**: `I couldn't confirm that from the documents....`

### 4.3 ALL-PASS (All Models Passed)

Total ALL-PASS Questions: **7**

- `Q5`: What insurance plans are accepted? (4B: 22.33s, beam: 25.86s, NVIDIA: 164.94s)
- `Q7`: How many business days in advance must outpatient surgery prior auths be submitted? (4B: 12.78s, beam: 19.62s, NVIDIA: 183.42s)
- `Q8`: How recent must clinical consultation notes be when submitting an auth request? (4B: 10.41s, beam: 17.38s, NVIDIA: 113.86s)
- `Q10`: What is the copayment amount for acupuncture therapy sessions at Horizon Valley? (4B: 10.05s, beam: 14.7s, NVIDIA: 53.11s)
- `Q11`: What is the discounted parking rate for patients in the medical plaza garage? (4B: 9.7s, beam: 11.08s, NVIDIA: 41.92s)
- `Q12`: What is the standard SLA for Apex Health Insurance diagnostic imaging? (4B: 17.77s, beam: 23.11s, NVIDIA: 224.14s)
- `Chain-T3`: how recent must they be? (4B: 15.05s, beam: 22.28s, NVIDIA: 415.92s)

## 5. Vision / Extraction Comparison Grid

### 5.1 Vision Benchmark Summary

| Vision Model | Correct | Correctly Blank | Missed | False Fill | Total Fields | Accuracy (%) | False Fill Rate (%) | Avg Latency (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `qwen3-vl:4b` | 20 | 3 | 2 | 0 | 25 | 92.0% | 0.0% | 40.18s |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | 17 | 3 | 4 | 1 | 25 | 80.0% | 4.0% | 191.36s |

### 5.2 Detailed Field-by-Field Vision Grid

| Model | Document | Field Name | Expected State | Expected Value | Extracted State | Extracted Value | Outcome |
|---|---|---|---|---|---|---|:---:|
| `qwen3-vl:4b` | invalid_npi_fax.pdf | patient_name | FILLED_HIGH | Casey Bennett | FILLED_HIGH | Casey Bennett | **correct** |
| `qwen3-vl:4b` | invalid_npi_fax.pdf | authorized_medication | FILLED_HIGH | Amoxicillin 500mg | CONFLICTING | null | **missed** |
| `qwen3-vl:4b` | invalid_npi_fax.pdf | authorization_date | FILLED_HIGH | 2026-02-14 | FILLED_HIGH | 2026-02-14 | **correct** |
| `qwen3-vl:4b` | invalid_npi_fax.pdf | chart_number | FILLED_HIGH | MRN-1102-K | FILLED_HIGH | MRN-1102-K | **correct** |
| `qwen3-vl:4b` | invalid_npi_fax.pdf | prescriber_npi | INVALID | 1982736451 | INVALID | 1982736451 | **correct** |
| `qwen3-vl:4b` | skewed_fax.pdf | patient_name | FILLED_HIGH | Alex Morgan | FILLED_HIGH | Alex Morgan | **correct** |
| `qwen3-vl:4b` | skewed_fax.pdf | attending_physician | FILLED_HIGH | Dr. Evelyn Reed | FILLED_MEDIUM | Dr. Evelyn Reed | **correct** |
| `qwen3-vl:4b` | skewed_fax.pdf | prescription_dosage | FILLED_HIGH | Take 1 capsule by mouth three times daily for 10 days. | FILLED_HIGH | Take 1 capsule by mouth three times daily for 10 days. | **correct** |
| `qwen3-vl:4b` | skewed_fax.pdf | chart_number | FILLED_HIGH | MRN-4412-A | FILLED_HIGH | MRN-4412-A | **correct** |
| `qwen3-vl:4b` | skewed_fax.pdf | authorized_medication | FILLED_HIGH | Amoxicillin 500mg | CONFLICTING | null | **missed** |
| `qwen3-vl:4b` | skewed_fax.pdf | authorization_date | FILLED_MEDIUM | 2026-02-14 | FILLED_HIGH | 2026-02-14 | **correct** |
| `qwen3-vl:4b` | skewed_fax.pdf | refill_count | FILLED_MEDIUM | 0 | FILLED_HIGH | 0 | **correct** |
| `qwen3-vl:4b` | skewed_fax.pdf | clinic_phone | FILLED_MEDIUM | 555-0145 | FILLED_HIGH | 555-0145 | **correct** |
| `qwen3-vl:4b` | skewed_fax.pdf | prescriber_signature | UNREADABLE | null | NOT_PRESENT | null | **correctly_blank** |
| `qwen3-vl:4b` | skewed_fax.pdf | prescriber_npi | NOT_PRESENT | null | NOT_PRESENT | null | **correctly_blank** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | patient_name | FILLED_HIGH | Jordan Taylor | FILLED_HIGH | Jordan Taylor | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | procedure_ordered | FILLED_HIGH | MRI lumbar spine | FILLED_HIGH | MRI lumbar spine | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | appointment_date | FILLED_HIGH | 2026-03-01 | FILLED_HIGH | 2026-03-01 | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | facility_location | FILLED_MEDIUM | Imaging Suite B | FILLED_HIGH | Imaging Suite B | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | referring_phone | FILLED_MEDIUM | 555-0170 | FILLED_HIGH | 555-0170 | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | clinical_justification | FILLED_HIGH | chronic lower back pain with radiculopathy | FILLED_MEDIUM | chronic lower back pain with radiculopathy | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | pain_scale_score | FILLED_HIGH | 7/10 | FILLED_HIGH | 7/10 | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | ordering_physician | FILLED_MEDIUM | Dr. Evelyn Reed | FILLED_MEDIUM | Dr. Evelyn Reed | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | prescriber_signature | FILLED_MEDIUM | Dr. Evelyn Reed | FILLED_MEDIUM | Dr. Evelyn Reed | **correct** |
| `qwen3-vl:4b` | faint_fax_2page.pdf | prescriber_npi | NOT_PRESENT | null | NOT_PRESENT | null | **correctly_blank** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | invalid_npi_fax.pdf | patient_name | FILLED_HIGH | Casey Bennett | FILLED_HIGH | Casey Bennett | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | invalid_npi_fax.pdf | authorized_medication | FILLED_HIGH | Amoxicillin 500mg | CONFLICTING | null | **missed** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | invalid_npi_fax.pdf | authorization_date | FILLED_HIGH | 2026-02-14 | FILLED_HIGH | 2026-02-14 | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | invalid_npi_fax.pdf | chart_number | FILLED_HIGH | MRN-1102-K | FILLED_HIGH | MRN-1102-K | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | invalid_npi_fax.pdf | prescriber_npi | INVALID | 1982736451 | INVALID | 1982736451 | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | patient_name | FILLED_HIGH | Alex Morgan | FILLED_MEDIUM | Alex Morgan | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | attending_physician | FILLED_HIGH | Dr. Evelyn Reed | FILLED_MEDIUM | Dr. Evelyn Reed | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | prescription_dosage | FILLED_HIGH | Take 1 capsule by mouth three times daily for 10 days. | FILLED_MEDIUM | Take 1 capsule by mouth three times daily for 10 days. | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | chart_number | FILLED_HIGH | MRN-4412-A | FILLED_MEDIUM | MRN-4412-A | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | authorized_medication | FILLED_HIGH | Amoxicillin 500mg | FILLED_MEDIUM | Amoxicillin 5oOmg | **false_fill** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | authorization_date | FILLED_MEDIUM | 2026-02-14 | FILLED_MEDIUM | 2026-02-14 | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | refill_count | FILLED_MEDIUM | 0 | FILLED_MEDIUM | 0 | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | clinic_phone | FILLED_MEDIUM | 555-0145 | FILLED_MEDIUM | 555-0145 | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | prescriber_signature | UNREADABLE | null | NOT_PRESENT | null | **correctly_blank** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | skewed_fax.pdf | prescriber_npi | NOT_PRESENT | null | NOT_PRESENT | null | **correctly_blank** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | patient_name | FILLED_HIGH | Jordan Taylor | FILLED_MEDIUM | Jordan Taylor | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | procedure_ordered | FILLED_HIGH | MRI lumbar spine | FILLED_MEDIUM | MRI lumbar spine | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | appointment_date | FILLED_HIGH | 2026-03-01 | FILLED_MEDIUM | 2026-03-01 | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | facility_location | FILLED_MEDIUM | Imaging Suite B | FILLED_MEDIUM | Imaging Suite B | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | referring_phone | FILLED_MEDIUM | 555-0170 | FILLED_MEDIUM | 555-0170 | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | clinical_justification | FILLED_HIGH | chronic lower back pain with radiculopathy | NOT_PRESENT | null | **missed** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | pain_scale_score | FILLED_HIGH | 7/10 | FILLED_MEDIUM | 7/10 | **correct** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | ordering_physician | FILLED_MEDIUM | Dr. Evelyn Reed | NOT_PRESENT | null | **missed** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | prescriber_signature | FILLED_MEDIUM | Dr. Evelyn Reed | NOT_PRESENT | null | **missed** |
| `nvidia:mistralai/mistral-large-4-1t-a52b` | faint_fax_2page.pdf | prescriber_npi | NOT_PRESENT | null | NOT_PRESENT | null | **correctly_blank** |

## 6. Exact Text of Failed Answers

### 1. [Q1] Model: `qwen3-vl:4b`
**Question**: HORIZON VALLEY HEALTH CLINIC data please
**Diagnostic Trace**: State: WEAK, Sources: []

```text
I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times.xlsx
- accepted_plan
```

### 2. [Q1] Model: `beam:cloud`
**Question**: HORIZON VALLEY HEALTH CLINIC data please
**Diagnostic Trace**: State: WEAK, Sources: []

```text
I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times.xlsx
- accepted_plan
```

### 3. [Q1] Model: `nvidia:mistralai/mistral-large-4-1t-a52b`
**Question**: HORIZON VALLEY HEALTH CLINIC data please
**Diagnostic Trace**: State: WEAK, Sources: []

```text
I couldn't find that in this assistant's documents. I do have information on:
- pa_turnaround_times.xlsx
- accepted_plan
```

### 4. [Q2] Model: `nvidia:mistralai/mistral-large-4-1t-a52b`
**Question**: What is the front desk intake fax number?
**Diagnostic Trace**: State: RELIABLE_TEXT, Sources: []

```text
I couldn't confirm that from the documents.
```

### 5. [Q3] Model: `nvidia:mistralai/mistral-large-4-1t-a52b`
**Question**: How early must patients arrive for a routine visit?
**Diagnostic Trace**: State: RELIABLE_TEXT, Sources: ['front_desk_policy_v1.pdf', 'front_desk_policy_v2.pdf', 'prior_auth_guide.pdf']

```text
I couldn't confirm that from the documents.
```

### 6. [Q4] Model: `qwen3-vl:4b`
**Question**: Summarize the outpatient prior authorization guide
**Diagnostic Trace**: State: RELIABLE_TEXT, Sources: ['front_desk_policy_v1.pdf', 'prior_auth_guide.pdf']

```text
- The outpatient prior authorization submission guide requires providers to submit requests through the provider portal
```

### 7. [Q4] Model: `nvidia:mistralai/mistral-large-4-1t-a52b`
**Question**: Summarize the outpatient prior authorization guide
**Diagnostic Trace**: State: RELIABLE_TEXT, Sources: ['front_desk_policy_v1.pdf', 'front_desk_policy_v2.pdf', 'fee_schedule.pdf', 'prior_auth_guide.pdf']

```text
I couldn't confirm that from the documents.
```

### 8. [Q6] Model: `nvidia:mistralai/mistral-large-4-1t-a52b`
**Question**: What is the effective date for our outpatient prior authorization policy?
**Diagnostic Trace**: State: RELIABLE_TEXT, Sources: []

```text
I couldn't confirm that from the documents.
```

### 9. [Q9] Model: `nvidia:mistralai/mistral-large-4-1t-a52b`
**Question**: What is the policy reference code for outpatient prior authorization?
**Diagnostic Trace**: State: RELIABLE_TEXT, Sources: []

```text
I couldn't confirm that from the documents.
```

### 10. [Chain-T1] Model: `nvidia:mistralai/mistral-large-4-1t-a52b`
**Question**: How many business days in advance must outpatient surgery prior auths be submitted?
**Diagnostic Trace**: State: RELIABLE_TEXT, Sources: ['front_desk_policy_v2.pdf', 'prior_auth_guide.pdf']

```text
I couldn't confirm that from the documents.
```

### 11. [Chain-T2] Model: `nvidia:mistralai/mistral-large-4-1t-a52b`
**Question**: and what documents are required with it?
**Diagnostic Trace**: State: RELIABLE_TEXT, Sources: ['front_desk_policy_v2.pdf', 'prior_auth_guide.pdf']

```text
I couldn't confirm that from the documents.
```

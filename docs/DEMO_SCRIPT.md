# Product Owner Demo Script

This script walks through the end-to-end staff-facing knowledge assistant demo on `desktop-gpu`.
It covers uploading the golden evidence files in **Data sources**, verifying ingestion status, and running grounded Q&A on **Home**.

---

## 1. Prerequisites & Environment Check

1. Confirm PostgreSQL and Ollama are active with the local models loaded:
   - Vision & answer model: `qwen3-vl:4b`
   - Embeddings: `bge-small-en-v1.5`
   - Reranker: `bge-reranker-large`
2. Ensure the backend and Next.js frontend are running (or launch via `python backend/scripts/run_server.py`).

---

## 2. Document Uploads (Data Sources)

In the web interface, navigate to **Data sources** for the assistant and upload the following 5 golden files located in `evals/golden/docs/`:

| # | File Name | Document Type | Key Content / Testing Purpose |
|---|---|---|---|
| 1 | `prior_auth_guide.pdf` | Multipage Digital PDF | Outpatient surgery submission windows, clinical note recency, appeal contacts |
| 2 | `skewed_fax.pdf` | Rotated Scanned Fax (Image PDF) | Scanned urgent prescription refill by Dr. Evelyn Reed for Alex Morgan (tests vision fallback pipeline) |
| 3 | `front_desk_policy_v1.pdf` | Digital PDF SOP | Check-in protocol arrival window (15 minutes), intake fax line |
| 4 | `fee_schedule.pdf` | Table / Digital PDF | Procedure CPT codes, descriptions, and fees (e.g. CPT 99213 = $135.00) |
| 5 | `accepted_plans.csv` | Structured Spreadsheet | Payer tiers, copays, and annual deductibles (tests tabular database extraction) |

### Verifying Ingestion Readiness
Wait until all documents show the green **Ready** badge in the UI.

To inspect the underlying database state and ensure chunks, embeddings, vision readings, and table rows are persisted, run:
```bash
python backend/scripts/kb_status.py --assistant-id <ASSISTANT_UUID>
```
All documents should report non-zero chunks (or table rows for CSV/XLSX), with 0 un-embedded chunks.

---

## 3. Demo Questions & Expected Answers (Home)

Navigate to **Home** and ask the following questions in sequence:

### Question 1: Prior Authorization Policy Code
- **Question:** `What is the policy reference code for outpatient prior authorization?`
- **Expected Answer:** `Policy 2026-PA-01` (or `2026-PA-01`)
- **Expected Source Chip:** `prior_auth_guide.pdf` (Page 1)

### Question 2: Policy Effective Date
- **Question:** `What is the effective date for our outpatient prior authorization policy?`
- **Expected Answer:** `January 1, 2026`
- **Expected Source Chip:** `prior_auth_guide.pdf` (Page 1)

### Question 3: Advance Submission Window
- **Question:** `How many business days in advance must outpatient surgery prior auths be submitted?`
- **Expected Answer:** `5 business days` (or `5 days`)
- **Expected Source Chip:** `prior_auth_guide.pdf` (Page 1)

### Question 4: Clinical Consultation Note Recency
- **Question:** `How recent must clinical consultation notes be when submitting an auth request?`
- **Expected Answer:** `30 days` (or `within past 30 days`)
- **Expected Source Chip:** `prior_auth_guide.pdf` (Page 1)

### Question 5: Vision Recovery (Rotated Scanned Fax)
- **Question:** `What medication and strength did Dr. Evelyn Reed authorize for Alex Morgan's urgent refill?`
- **Expected Answer:** `Amoxicillin 500mg`
- **Expected Source Chip:** `skewed_fax.pdf` (Page 1)
- **Note:** This verifies visual recovery on rotated/scanned fax documents.

### Question 6: Front Desk Routine Arrival Window
- **Question:** `How many minutes prior to a scheduled routine visit are patients required to arrive for check-in?`
- **Expected Answer:** `15 minutes`
- **Expected Source Chip:** `front_desk_policy_v1.pdf` (Page 1)

### Question 7: Fee Schedule Lookup
- **Question:** `What is the standard fee for a 15-minute low complexity office visit (CPT 99213)?`
- **Expected Answer:** `$135.00`
- **Expected Source Chip:** `fee_schedule.pdf` (Page 1)

### Question 8: Tabular Data Lookup (Spreadsheet)
- **Question:** `What is the annual deductible for Crestview Health under the Gold Tier plan?`
- **Expected Answer:** `$500.00`
- **Expected Source Chip:** `accepted_plans.csv` (Row 1)
- **Note:** This verifies query routing to structured `tables_data`.

### Question 9: Data Inventory
- **Question:** `What data do you have?`
- **Expected Answer:** Fast deterministic listing of active documents and top section headings without hallucinating client facts or executing LLM calls:
  ```text
  This assistant has information from the following documents:
  - accepted_plans.csv
  - fee_schedule.pdf
  - front_desk_policy_v1.pdf
  - prior_auth_guide.pdf
  - skewed_fax.pdf
  ```
- **Expected Source Chip:** None

### Question 10: Graceful Refusal with Suggestions (No Evidence in Corpus)
- **Question:** `What is the copayment amount for acupuncture therapy sessions at Horizon Valley?`
- **Expected Answer:**
  ```text
  I couldn't find that in this assistant's documents. I do have information on:
  - accepted_plans.csv
  - fee_schedule.pdf
  - front_desk_policy_v1.pdf
  ```
- **Expected Source Chip:** None

### Question 11: Conversational Greeting
- **Question:** `hello`
- **Expected Answer:** Short friendly greeting stating no client facts (e.g., `Hello! How can I assist you today?`).
- **Expected Source Chip:** None

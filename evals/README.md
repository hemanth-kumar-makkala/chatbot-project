# Evaluation Framework (`evals/`)

## What Belongs Here
- **Golden Evaluation Dataset (`golden/`)**: Synthetic and strictly de-identified evaluation fixtures. Contains 20–30 synthetic documents and 100+ labelled question-answer-evidence pairs across text, visual recovery, tables, refusals, conflicts, multilingual, and extraction test categories.
- **Evaluation Reports (`reports/`)**: Saved evaluation metric reports, benchmark runs, and RAGAS score summaries generated during CI or local test runs.

## What Must NEVER Go Here
- Real client documents, real healthcare records, or real PHI under any circumstances.
- Proprietary or un-redacted documents from client pilot environments.
- Reports derived from non-de-identified data sources without compliance review.

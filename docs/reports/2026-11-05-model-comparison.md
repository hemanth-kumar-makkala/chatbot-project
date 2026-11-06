# Model Comparison — November 5, 2026

## Run status: Part B implementation complete; comparison not executed

Part A and the requested Part B provider/configuration work are implemented, but no model comparison requests were sent and this report contains **no accuracy, latency, refusal, invalid-JSON, or extraction measurements**. The existing `backend/scripts/compare_4b_vs_beam.py` reads `.env` and performs Alembic upgrades against a throwaway database; both actions are explicitly prohibited. The full `pytest -m "not models"` suite was not run to preserve the no-Alembic rule.

The requested model placeholders were not supplied and are recorded as skipped: `NVIDIA [MINIMAX ID]` and `Inception [MERCURY NAME]`. The raw JSON beside this report records the comparison as `not_executed`; it contains no fabricated result rows.

## Implemented work and verification

- Table row selection retains only the best-matching source document; CSV and XLSX header detection skips title/banner rows; table answers are rendered as Markdown from stored cells and real headers.
- Inventory routing is limited to explicit questions about the assistant's own document inventory; the comparison grader rejects unexpected table values.
- Cloud safety compares SHA-256 hashes of original stored bytes with local synthetic golden files and checks both answer and vision model selections.
- Added independent `ANSWER_MODEL` and `VISION_MODEL` settings, an OpenAI-compatible NVIDIA/Inception adapter, and provider routing. Hosted model selectors require their endpoint and API key settings or raise `ConfigurationError`.
- Added per-model/per-role output token limits for `beam:cloud`, the requested NVIDIA Mistral model, and hosted-provider fallbacks in the desktop and cloud profiles. Existing Qwen3-VL 4B limits remain unchanged.
- Focused migration-free unit tests covering table, inventory, grading, safety, model routing/settings, token limits, graph, and validation completed with exit code 0. All pre-commit hooks passed on `--all-files`.
- A broader exploratory unit run timed out at 420 seconds. A separate API-auth test has an incomplete fixture; after temporarily supplying its missing retention setting for diagnosis, it attempted a local PostgreSQL connection and failed without a password. That temporary fixture edit was reverted; no `.env` or credentials were accessed.

## Requested result grids

| Requested output | Result |
|---|---|
| Answer grid, seconds, and by-code correctness | Not available — no comparison run |
| Per-model accuracy, refusals, malformed JSON, average seconds | Not available — no comparison run |
| ALL-FAIL / SOME-FAIL / ALL-PASS classification and traces | Not available — no comparison run |
| Vision extraction outcome grid | Not available — no comparison run |
| Exact text of newly failed answers | None — no model requests were sent |

The comparison remains blocked until it can be run without opening `.env` or executing Alembic. No model placeholders were guessed. No live API, worker, web, or Ollama process was started or stopped.

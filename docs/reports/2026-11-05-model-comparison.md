# Model Comparison — November 5, 2026

## Run status: requested matrix incomplete

The requested comparison was started only after the full `not models` suite and all pre-commit hooks passed. The terminal execution limit expired at 420 seconds before the runner returned output or produced a completed comparison artifact. No result rows, timing values, accuracy scores, refusal counts, malformed-JSON counts, or extraction measurements from this attempt are available; none are inferred from older comparison artifacts.

The existing `backend/scripts/compare_4b_vs_beam.py` hard-codes two answer-model runs (`qwen3-vl:4b` and `beam:cloud`). It does not implement the requested NVIDIA answer-model run or independent vision-model matrix. Requested selectors: answer models `qwen3-vl:4b`, `beam:cloud`, and NVIDIA `mistralai/mistral-large-4-1t-a52b`; vision models `qwen3-vl:4b` and NVIDIA `mistralai/mistral-large-4-1t-a52b`.

## Root cause and repair

The test-suite regression was **not caused by dotenv settings being unavailable**: `HERMES_SKIP_DOTENV` was not set in the requested test command, and the run reached 80% before its first failure. The first failure was `tests/unit/test_table_and_conflict_validation.py::test_render_table_answer_compares_every_cell_to_stored_row_byte_for_byte`. The test still parsed the old `[Sheet: ..., Row: ...]` text representation, while `node_generate` had been changed to produce a Markdown table assembled from stored cells. Updated the regression test to assert the Markdown header, separator, full rows, and byte-for-byte cell equality.

Separately, `HERMES_SKIP_DOTENV` in `backend/app/db/env_utils.py` was an unnecessary guard introduced from the earlier misreading of the `.env` rule. Removed the guard and replaced its guard-specific test with a synthetic explicit-file test proving normal dotenv loading preserves existing environment values. No `.env` file was manually opened, printed, or read.

## Implemented work and verification

- Table row selection retains only the best-matching source document; CSV and XLSX header detection skips title/banner rows; table answers are rendered as Markdown from stored cells and real headers.
- Inventory routing is limited to explicit questions about the assistant's own document inventory; the comparison grader rejects unexpected table values.
- Cloud safety compares SHA-256 hashes of original stored bytes with local synthetic golden files and checks both answer and vision model selections.
- Added independent `ANSWER_MODEL` and `VISION_MODEL` settings, an OpenAI-compatible NVIDIA/Inception adapter, and provider routing. Hosted model selectors require their endpoint and API key settings or raise `ConfigurationError`.
- Added per-model/per-role output token limits for `beam:cloud`, the requested NVIDIA Mistral model, and hosted-provider fallbacks in the desktop and cloud profiles. Existing Qwen3-VL 4B limits remain unchanged.
- Focused migration-free unit tests covering table, inventory, grading, safety, model routing/settings, token limits, graph, and validation completed with exit code 0. All pre-commit hooks passed on `--all-files`.
- `pytest -m "not models"` with the requested timeout: **905 passed, 1 skipped, 0 failed, 0 errors**, exit 0. The first `-x` run reached 80% and identified the stale table-format assertion; no setup-error cascade or hang reproduced.
- Final targeted tests covering changed tests: 22 passed.
- `uv run --project backend pre-commit run --all-files`: all configured hooks passed.
- Comparison runner attempted after both gates passed; terminal tool timed out at 420 seconds before output/measurements were captured.

## Requested answer-model comparison grid

No current-run measurements were captured. `—` means unavailable, not zero. Historical data in `docs/comparison_4b_vs_beam.json` was not treated as a result of this attempt.

| Answer model | Correct / evaluated | Refusals | Malformed JSON | Average seconds | ALL-FAIL / SOME-FAIL / ALL-PASS | Traces / exact answer grid |
|---|---:|---:|---:|---:|---|---|
| `qwen3-vl:4b` | — | — | — | — | — | — |
| `beam:cloud` | — | — | — | — | — | — |
| NVIDIA `mistralai/mistral-large-4-1t-a52b` | — | — | — | — | — | — |

## Requested vision/extraction comparison grid

No vision-model grid or fax extraction completed in the captured run.

| Vision model | Extraction outcome / fields | False fills | Refusals / invalid JSON | Average seconds | Trace |
|---|---|---:|---|---:|---|
| `qwen3-vl:4b` | — | — | — | — | — |
| NVIDIA `mistralai/mistral-large-4-1t-a52b` | — | — | — | — | — |

## Failed-answer text

No newly failed answer text was captured from the timed-out run. This is not evidence that all answers passed; the comparison report is incomplete.

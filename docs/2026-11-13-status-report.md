# Client Knowledge Assistant (CKA): Status Report

**Date:** 13 November 2026
**Repo:** `chatbot-project` (GitHub)
**Machine used:** Office PC: Windows 11 Pro, i7-12700F, 32 GB RAM, RTX 3050 6 GB, 1 TB SSD
**Active runtime profile:** `desktop-gpu` (local Ollama models)
**Data used:** synthetic golden documents only (`evals/golden/docs`)

---

## 1. Summary

The system now works end to end on local models. In the live UI it:

- answers questions from the knowledge base with page citations,
- detects conflicting document versions and shows both sources,
- refuses when the documents do not contain the answer,
- answers general requests (greetings, email drafts) without citing documents,
- keeps chat-attached documents inside the chat only, extracts fields with a dual read (OCR and vision), and flags disagreements instead of guessing,
- answers questions about an attached document and refuses values that are not in it.

The full mock test suite passes (**1023 passed, 0 failed** at the last run). Several real bugs and safety holes were found and fixed during this work (section 5).

What is not done yet: a measured end-to-end evaluation, a few known bugs (section 6), user-chosen extraction fields, the trace viewer, the document inspector, deployment packaging and the approvals needed for cloud and real data (section 7).

**The project is proven to work. The next step is measuring how well it works.**

---

## 2. Current setup

### 2.1 Models (profile `desktop-gpu`)

| Role | Model | Notes |
|---|---|---|
| Router, answer | `qwen3:8b` (Ollama) | Thinking off (`think: false`), JSON schema output, `num_ctx` 6144 |
| Vision, extract, judge | `qwen3-vl:4b-instruct` (Ollama) | Must be the **instruct** tag. The plain `qwen3-vl:4b` tag ignores `think: false` (86 s vs 8 s on the same fax) |
| Embeddings | BGE-M3 (local, CPU) | 1024-dim vectors, matches the database |
| Reranker | BGE reranker v2 m3 (local, CPU) | Unchanged |
| OCR / parsing | Docling (local) | Unchanged |

Measured on this PC:

- `qwen3:8b` does not fit fully in 6 GB VRAM: about 30 to 37 percent runs on CPU. Short replies take about 3 to 5 s.
- `qwen3-vl:4b-instruct` reads the synthetic fax in about 8 s with no thinking.
- The two models cannot sit in VRAM together. Ollama swaps them, so the first vision or judge call after a text call is slower.

Ollama settings (Windows user environment variables): `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`.

### 2.2 Other profiles

| Profile | Purpose | State |
|---|---|---|
| `mock` | Tests | Working |
| `desktop-gpu` | Local models (current) | Working |
| `desktop-api` | Gemini via AI Studio (free tier) | Configured, kept as cloud fallback; free tier only gives Flash models; synthetic data only |
| `cloud` | Planned GCP / Vertex | Not built (Phase 6) |
| `home-gpu`, `office-cpu` | Older local profiles | Reference old models; not maintained |

Providers in the catalog: **Ollama and Gemini only**. NVIDIA, Inception, OpenAI, Anthropic and Vertex placeholders were removed. The engine stays model-agnostic: adding a provider later is a catalog entry plus its settings.

### 2.3 Ports and services

| Service | Address |
|---|---|
| API | http://127.0.0.1:8010 (8000 is used by another project) |
| Web | http://127.0.0.1:3000 |
| Postgres (Docker, `cka-postgres`) | 127.0.0.1:5432 |
| Ollama | http://127.0.0.1:11434 |

---

## 3. What we did (11 to 13 November)

1. **Briefing and audit of the repo.** Read-only reports on the codebase and the model layer.
2. **Model layer rebuilt.**
   - Rate-limit retries and pacing.
   - Image scaling for API models.
   - Five roles routed separately (router, answer, vision, extract, judge).
   - Token budgets fixed.
   - Reasoning output stripped.
   - Report stamping (profile, models, machine, commit) in eval reports.
3. **Safety fixes** (details in section 5).
4. **Provider experiments.**
   - NVIDIA free endpoints were unreliable: 503 errors, models listed but not served, and models that think by default.
   - The Gemini free tier only gives Flash models.
   - Switched to local Ollama.
5. **Gemini and Ollama structured output.** Both now get the real JSON schema and thinking switched off, set per provider in the catalog.
6. **Router prompt.** Explicit rules and examples for general, knowledge, table, extraction and session-document questions.
7. **Spreadsheet ingestion.** Title rows are detected and skipped, real headers are used, and citations show sheet and row.
8. **Table answers.** Short direct answers instead of dumping the whole table, and an explicit note when a term (for example "urgent") is not in the table.
9. **Chat document UX.**
   - Attaching a file shows a card; nothing runs automatically.
   - An **Extract fields** button runs extraction.
   - Questions about the attached document are answered from that document only.
10. **UI fixes.**
    - No source chips on refusals or general replies.
    - A clear "No supporting evidence" line on refusals.
    - Line breaks preserved.
    - Doubled punctuation fixed in conflict answers.
11. **Planning documents written** (in `cka-data\tmp`, not in git): the trace viewer plan, the model selection plan and the document inspector plan.

---

## 4. What works (verified in the live UI)

| Test | Result |
|---|---|
| "hello" | General reply, no sources |
| Email draft about a delayed prior authorization | Real draft, no sources, line breaks kept |
| Patient Check-In Protocol | Answers 20 min (v2), notes v1 said 15 min, cites both |
| Referral fax number | Answers 555-0102 (v2), notes v1 said 555-0101, cites both |
| Parking policy (not in documents) | Refuses, no source chips |
| Upload of golden documents | Ingested and searchable |
| Fax extraction (`skewed_fax.pdf`) | Patient, chart number, date, refill count, phone and clinician correct. Medication flagged CONFLICTING ("5oOmg" OCR vs "500mg" vision): the dual read working as designed |
| Date of birth question on the attached fax | Refuses: "I couldn't confirm that from the attached document" (the fax has no DOB) |
| Document card on attach | Shows file, page count, retention notice, Extract fields button |

---

## 5. Bugs and safety holes found and fixed

| Problem | Impact | Status |
|---|---|---|
| Cloud safety lock could be skipped when `chat_model` was empty | Non-synthetic documents could reach a non-BAA model | Fixed, fails closed |
| Unknown models guessed by name (for example "nvidia" in the name) | A remote model with an unusual name skipped the lock | Fixed: unknown model is an error |
| Tenant check on evidence was dead code (no `tenant_id` on evidence) | One of two isolation layers was off; only database RLS protected | Fixed, enforced |
| Conflict judge never ran (read a field that did not exist) | Conflicts between documents could be missed | Fixed; judge failure now fails closed |
| Fake citations created for unknown chunk IDs | Answers could cite sources that do not exist | Removed |
| Fax upload route fell back to a default tenant | Uploads could land in the wrong tenant | Removed |
| Hard-coded `qwen3-vl:4b` defaults | Wrong model used silently | Removed |
| Model environment variables could override the profile | Two sources of truth for models | Removed |
| Token budget cut answers mid-JSON | Refusals instead of answers | Fixed |
| Patient name pattern crossed lines | Wrong extracted values | Fixed |
| Migrations disabled app loggers in tests | One test failed only in the full suite | Fixed |
| Router fell back to the knowledge route on every failure | Greetings and drafts were refused | Fixed by working structured output and router rules |

---

## 6. Known issues (not fixed yet)

1. **Dosage OCR read on the skewed fax** still captures the patient and refill lines. The fax is skewed, so OCR line order is likely scrambled. It is safely flagged as CONFLICTING, but the OCR side is wrong. Fix in progress (Stage M).
2. **"what is the dosage in this fax?" goes to the clarify route** instead of the attached-document route. Fix in progress (Stage M).
3. **The first Extract fields click after a restart** shows "Received an unexpected response format", while later clicks work. This is likely a frontend timeout during the Ollama model swap. Fix in progress (Stage M).
4. **Spreadsheet header detection** needs re-testing after deleting and re-uploading `pa_turnaround_times.xlsx`. The old upload still shows "Column_2" headers.
5. **Speed.** About 30 percent of `qwen3:8b` runs on CPU on this 6 GB card. Longer answers and model swaps add delay.
6. **Settings ignore unknown environment variables** (needed so old NVIDIA lines in `.env` do not break startup). A typo in a key name will not be reported until that provider is used.
7. **`GraphRuntimeContext` still has a default synthetic tenant.** Production routes set the tenant explicitly. Removing the default touches about 40 test files, so it is deferred.
8. **Browser console errors** about a "message channel closed" come from a Chrome extension, not the app.

---

## 7. What still needs to be done

### 7.1 Next (in progress or queued)

1. **Stage M:**
   - route attached-document questions correctly
   - extraction timeout and messages
   - the dosage OCR read
2. **Stage N: end-to-end evaluation on `desktop-gpu`.**
   - The golden set plus 12 new cases.
   - Pass, fail or unclear per case, with the failing layer: routing, retrieval, evidence gate, generation, validation, extraction or UI.
   - Latency figures.
   - Results go to `evals/reports/`.
3. **Stage L: user-chosen fields.**
   - "extract patient name and medication" extracts only those fields.
   - The dual read and field states are kept for custom fields.

### 7.2 After that

4. **Trace viewer.** The plan is ready. Its stage 1 also connects the tracer, which currently records no steps for live questions.
5. **Document inspector.** The plan is ready. It shows, per document, the extracted text, OCR confidence, chunks, embeddings and spreadsheet parsing.
6. **Golden set growth** to the spec's 20 to 30 documents and 100+ questions. The eval test currently uses about 30 questions.
7. **Per-assistant model selection** (stages 2 to 4 of the model selection plan): choose answer and judge models per assistant in the UI.
8. **Remove the default tenant** from `GraphRuntimeContext`. This is a large mechanical test update.

### 7.3 Before any deployment

9. **Packaging:**
   - working Dockerfiles and `docker-compose.yml` for the API, worker and web
   - a test service that runs the suite in Linux, matching CI
10. **Authentication:** proper login for 100 to 300 staff (JWT, SSO or similar). It is cookie-only today.
11. **Cloud adapters (Phase 6):**
    - a Vertex AI model adapter
    - a GCS storage adapter
    - a malware scanner
    - fixing the `cloud.yaml` embedding dimension (768 vs the database's 1024)
12. **Approvals:**
    - CEO sign-off
    - a GCP project with Vertex enabled
    - written BAA coverage for each service used
    - retention periods for chats, uploads and logs
    - a pilot client and its BAA
    - an MFA or SSO policy

### 7.4 Product ideas raised

- Per-tenant model choice, so non-HIPAA teams (HR, social media) can use cheaper models.
- Internet access for non-HIPAA tenants only.
- Agent mode later (entering extracted fields into EMR or portals with human approval).

---

## 8. Working rules that proved important

- **Agents claim success that is not real.** Earlier examples: fake citations, `getattr` fallbacks, tests that set fields by hand, a judge that never ran. Always verify yourself:
  - the full test suite summary line (0 failed)
  - pre-commit run twice (second run all Passed)
  - the file list
- **Give agents small tasks with file caps.** Let them stop and list when they need more files. Updating test fixtures to a new contract is not weakening a test.
- **Run tests with `uv run python -m pytest`,** never `uv run pytest`.
- **For long unattended runs, use a queue:** stages with caps, the full suite after each stage, stop on repeated failure, and a report to `queue_report.md`.
- **Commit after every verified stage.** Agents never run git.
- **Test in the UI.** Several bugs only showed up there:
  - the wrong port
  - the wrong model profile
  - the triple extraction
  - the stuck status line

---

## 9. Daily start

Before starting: Docker Desktop and Ollama must be running, and old API, worker and web windows must be closed.

Postgres:

```powershell
cd "$env:USERPROFILE\Downloads\Projects\chatbot-project"
docker compose up -d postgres
```

Window 1, API (port 8010):

```powershell
$P = "$env:USERPROFILE\Downloads\Projects"; $D = "$P\cka-data"
$env:HF_HOME="$D\hf"; $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:TEMP="$D\tmp"; $env:TMP="$D\tmp"
$env:OLLAMA_BASE_URL="http://127.0.0.1:11434"; $env:APP_PROFILE="desktop-gpu"; $env:COOKIE_SECURE="false"; $env:W_STORAGE="$D\storage\files"
cd "$P\chatbot-project\backend"
uv run python -c "import asyncio, uvicorn, os; from app.api.app import create_app; from app.api.config import ApiSettings; app = create_app(ApiSettings(profile='desktop-gpu', storage_dir=os.environ['W_STORAGE'], cookie_secure=False)); server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8010)); asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)"
```

Window 2, worker:

```powershell
$P = "$env:USERPROFILE\Downloads\Projects"; $D = "$P\cka-data"
$env:HF_HOME="$D\hf"; $env:HF_HUB_OFFLINE="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:TEMP="$D\tmp"; $env:TMP="$D\tmp"
$env:OLLAMA_BASE_URL="http://127.0.0.1:11434"; $env:APP_PROFILE="desktop-gpu"
cd "$P\chatbot-project\backend"
uv run python -m procrastinate --app=app.worker.app worker
```

Window 3, web:

```powershell
cd "$env:USERPROFILE\Downloads\Projects\chatbot-project\web"
$env:API_ORIGIN="http://127.0.0.1:8010"; $env:NEXT_PUBLIC_DATA_SOURCE="live"
npm run dev -- -H 127.0.0.1
```

The same commands are saved as scripts in `cka-data\scripts` (`api.ps1`, `worker.ps1`, `web.ps1`, `start-all.ps1`), if those were created.

Checks before every commit:

```powershell
cd "$env:USERPROFILE\Downloads\Projects\chatbot-project\backend"
$env:APP_PROFILE="mock"
uv run python -m pytest -m "not models" 2>&1 | Select-Object -Last 3
cd ..
git add -A
uv run --project backend pre-commit run --all-files
git add -A
uv run --project backend pre-commit run --all-files
git commit -m "<task>" -m "Agent: antigravity"
git push
```

---

## 10. Moving to another PC

### 10.1 What is in GitHub, and what is not

| Item | In GitHub? | What to do on the new PC |
|---|---|---|
| Source code, tests, prompts, profiles, catalog, docs, golden synthetic documents | Yes (once committed and pushed) | `git clone` |
| `.env` (keys, database settings) | **No, and it must never be** | Copy it securely (USB or password manager), never by email or chat. Rotate any key that was ever exposed |
| `cka-data\hf` (BGE-M3, reranker) | No | Copy the folder, or let it download once with offline mode off |
| `cka-data\docling-models` | No | Copy the folder, or download again |
| `cka-data\storage\files` (uploaded files) | No | Not needed; re-upload the synthetic golden documents |
| `cka-data\tmp` (reports, plans, logs) | No | Copy the plan and report files you want to keep (trace viewer plan, document inspector plan, model selection plan, queue reports) |
| `cka-data\scripts` (start scripts) | No | Copy, or recreate from section 9 |
| Postgres data (Docker volume) | No | Start fresh (run migrations and the dev seed, then re-upload documents), or move it with the backup and restore scripts |
| Ollama models | No | `ollama pull qwen3:8b` and `ollama pull qwen3-vl:4b-instruct` |
| `backend\.venv`, `web\node_modules`, `web\.next` | No, and **do not copy them** | Rebuild with `uv sync` and `npm install` (they are machine-specific) |

### 10.2 Can you just copy the whole folder and connect to Git?

It works if the copy includes the hidden `.git` folder, but a **fresh clone is safer**. Copying brings across the machine-specific `.venv` and `node_modules`, and any half-finished uncommitted changes. Recommended:

1. On the **old** PC, make sure nothing is lost:
   ```powershell
   cd "$env:USERPROFILE\Downloads\Projects\chatbot-project"
   git status                              # must say "nothing to commit, working tree clean"
   git branch -a                           # see every branch
   git log origin/main..main --oneline     # must print nothing (everything pushed)
   git push --all                          # push every branch, to be safe
   ```
   If you work on a branch such as `wip/model-layer`, check it the same way, or merge it into `main` first (after CI is green).
2. On the **new** PC:
   ```powershell
   cd "$env:USERPROFILE\Downloads\Projects"
   git clone <your GitHub repo URL> chatbot-project
   cd chatbot-project
   git checkout <the branch you work on>
   ```
3. Copy `.env` into the repo root. Copy `cka-data` (at least `hf`, `docling-models` and `scripts`) to `Downloads\Projects\cka-data`.
4. Rebuild and prepare:
   ```powershell
   cd backend; uv sync; cd ..
   cd web; npm install; cd ..
   docker compose up -d postgres
   cd backend; uv run alembic upgrade head; uv run python scripts/seed_dev_data.py; cd ..
   ollama pull qwen3:8b
   ollama pull qwen3-vl:4b-instruct
   ```
5. Run the test suite and pre-commit (section 9) before changing anything.

### 10.3 Things to watch on another PC

- **Windows Smart App Control** blocked the project's Python on this PC when it switched itself on. If you see `os error 4551`, that is the cause. Turning it off cannot be undone without reinstalling Windows, so check with IT on company machines. Running in Docker avoids it.
- **PowerShell execution policy:** run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` once if npm or the scripts are blocked.
- **GPU differences.**
  - The laptop's RTX 3060 (laptop version) also has 6 GB, so the same models apply.
  - A card with 12 GB or more can hold both models and run fully on the GPU.
  - Check with `nvidia-smi`.
- **Ports.** If something else uses 8010 or 3000, change the port in the API command and in `API_ORIGIN`.
- **Ollama environment variables** (`OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`) are set per machine.
- **Folder paths.** The commands use `$env:USERPROFILE\Downloads\Projects`. Keep the same layout, or update the paths.
- **Results differ by machine.** Eval and load-test numbers depend on the GPU and the models. Reports are stamped with the machine and models; compare like with like.
- **Agents and their memory.** Antigravity and Hermes conversations do not move with the repo. `AGENTS.md`, `docs/STATUS.md` and `docs/BUILD_LOG.md` carry the project context, and they are in git.

---

## 11. Open decisions

- Which machine is the main development machine going forward (office PC or laptop).
- Whether to keep Gemini (`desktop-api`) as a cloud fallback, or remove it until Vertex.
- When to merge the work branch into `main` (after CI is green).
- Cost target for the cloud setup (spec under $100 a month; earlier estimates $145 to $175 for lean GCP), compared with the current Chatbase cost.

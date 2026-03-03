# 🤖 Chatbot Project — Notes & Discussion Log

> **Purpose**: Central place for all project discussions, implementation details, decisions, updates, and remarks.
> **Last Updated**: 2026-03-02

---

## 📌 Project Overview

A **local multi-tenant SaaS RAG application** (Chatbase clone) built with Python and Streamlit.
Users can sign up, create custom chatbots, upload PDFs, and chat with their bots using RAG.

### Tech Stack

| Layer              | Technology          |
|--------------------|---------------------|
| UI / Backend       | Streamlit           |
| Relational DB      | SQLite (`chatbot.db`) |
| Vector Storage     | ChromaDB (`chroma_data/`) |
| RAG Orchestration  | LlamaIndex          |
| PDF Parsing        | LlamaParse          |
| LLM & Embeddings   | Google Gemini       |

### Project Structure

```
chatbot-project/
├── app.py                  # Entry point / Streamlit main app
├── database.py             # SQLite schema, DB helpers
├── rag_engine.py           # RAG logic (LlamaIndex + ChromaDB)
├── vector_store.py         # ChromaDB client setup / helpers
├── .env                    # API keys (GEMINI_API_KEY, etc.)
├── .streamlit/
│   └── config.toml         # Streamlit config
├── pages/
│   ├── auth.py             # Login / signup UI
│   ├── dashboard.py        # Bot management dashboard
│   ├── bot_management.py   # Create/edit bots, upload PDFs
│   └── chat.py             # Chat interface (RAG chat per bot)
├── chatbot.db              # SQLite database file
├── chroma_data/            # Persisted ChromaDB vector store
├── requirements.txt        # Python dependencies
└── venv/                   # Virtual environment
```

---

## 🚀 How to Run

```bash
# 1. Activate virtual environment (Windows)
venv\Scripts\activate

# 2. Run the app
streamlit run app.py
```

---

## 📋 Implementation Log

### Session 1 — 2026-02-27 | Initial Build
**Status**: ✅ Completed

- Set up project structure and all core files.
- Implemented SQLite schema via `database.py` (users, bots, documents tables).
- Initialized persistent ChromaDB client in `vector_store.py`.
- Built Streamlit pages: `auth.py`, `dashboard.py`, `bot_management.py`, `chat.py`.
- Implemented RAG pipeline in `rag_engine.py` using LlamaIndex + Gemini.
- Added **bot-level RAG isolation**: vector store retriever filters by `bot_id` metadata to prevent data bleed between bots.
- Configured `rag_engine.py` to dynamically load each bot's custom system prompt.
- Set up `.env` for API key management (Google Gemini).
- Configured `.streamlit/config.toml` for app settings.

---

### Session 2 — 2026-02-27 | Running the App
**Status**: ✅ Completed

- Walked through activating the virtual environment on Windows.
- Confirmed `streamlit run app.py` as the launch command.

---

### Session 3 — 2026-03-02 | Project Notes Setup
**Status**: ✅ Completed

- Created this `PROJECT_NOTES.md` file for centralizing all discussions and updates.

---

## 🐛 Known Issues / Bugs

> Add bugs/issues here as they are discovered.

| # | Issue | Status | Notes |
|---|-------|--------|-------|
| - | *(none logged yet)* | - | - |

---

## 💡 Ideas / Feature Backlog

> Add feature ideas, enhancements, and future work here.

| Priority | Feature | Notes |
|----------|---------|-------|
| - | *(none logged yet)* | - |

---

## 🔑 Environment Variables (`.env`)

> Do NOT commit `.env` to version control.

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Google Gemini API key for LLM and embeddings |
| *(add others as needed)* | |

---

## 📝 Remarks & Decisions

> Use this section to log any important architectural decisions, trade-offs, or discussion outcomes.

- **Why ChromaDB?** Lightweight, local, persistent — no external vector DB needed for local SaaS.
- **Why SQLite?** Simple relational storage; no server needed. Sufficient for a local multi-tenant app.
- **RAG Isolation Strategy**: Each bot's documents are indexed with `bot_id` metadata. At query time, the retriever filters by `bot_id` to ensure bots only see their own data.

---

*Keep this file updated after every working session.*

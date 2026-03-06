"""
database.py — SQLite schema and CRUD helpers for the BYOK RAG SaaS platform.

Schema:
  users     → user accounts with werkzeug-hashed passwords
  bots      → chatbots owned by users with custom system prompts, provider & model
  documents → PDF records per bot with processing status and page count
  api_keys  → per-provider encrypted API keys (multi-key BYOK vault)

Security:
  - Passwords are hashed with werkzeug (PBKDF2 + salt), never stored plain.
  - API keys are stored as Fernet-encrypted BLOBs. Raw keys never touch this file.

Concurrency:
  - WAL journal mode for concurrent reads/writes.
  - busy_timeout of 5 seconds to handle lock contention gracefully.
"""
import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = Path(__file__).parent / "chatbot.db"


def _get_conn() -> sqlite3.Connection:
    """Open a SQLite connection with row-factory, foreign keys, WAL mode, and busy timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they don't already exist, and run migrations."""
    conn = _get_conn()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username          TEXT    UNIQUE NOT NULL,
                password_hash     TEXT    NOT NULL,
                encrypted_api_key BLOB,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bots (
                bot_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                bot_name      TEXT    NOT NULL,
                system_prompt TEXT    NOT NULL DEFAULT '',
                provider      TEXT    NOT NULL DEFAULT 'google',
                model_name    TEXT    NOT NULL DEFAULT 'gemini-2.5-flash',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS documents (
                doc_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id      INTEGER NOT NULL REFERENCES bots(bot_id) ON DELETE CASCADE,
                filename    TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending',
                page_count  INTEGER,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                key_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                provider      TEXT    NOT NULL,
                encrypted_key BLOB    NOT NULL,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, provider)
            );
        """)

    # --- Migrations for existing databases ---
    _run_migrations(conn)
    conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """
    Safely add new columns and migrate data from legacy schema.
    Each migration checks before applying, so it is idempotent.
    """
    cursor = conn.execute("PRAGMA table_info(bots)")
    bot_columns = {row[1] for row in cursor.fetchall()}

    with conn:
        # Add provider/model_name columns to bots if missing
        if "provider" not in bot_columns:
            conn.execute("ALTER TABLE bots ADD COLUMN provider TEXT NOT NULL DEFAULT 'google'")
        if "model_name" not in bot_columns:
            conn.execute("ALTER TABLE bots ADD COLUMN model_name TEXT NOT NULL DEFAULT 'gemini-2.5-flash'")

        # Migrate legacy single-key from users.encrypted_api_key → api_keys table
        try:
            rows = conn.execute(
                "SELECT user_id, encrypted_api_key FROM users WHERE encrypted_api_key IS NOT NULL"
            ).fetchall()
            for row in rows:
                user_id = row["user_id"]
                blob = row["encrypted_api_key"]
                if blob is None:
                    continue
                # Only migrate if no google key exists yet in api_keys
                existing = conn.execute(
                    "SELECT 1 FROM api_keys WHERE user_id = ? AND provider = 'google'",
                    (user_id,),
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO api_keys (user_id, provider, encrypted_key) VALUES (?, 'google', ?)",
                        (user_id, bytes(blob)),
                    )
        except Exception:
            pass  # Legacy column may not exist on fresh DBs — safe to skip


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def register_user(username: str, password: str) -> dict | None:
    """
    Insert a new user with a hashed password.
    Returns the new user row on success, or None if username is taken.
    """
    password_hash = generate_password_hash(password)
    conn = _get_conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username.strip(), password_hash),
            )
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        return None  # username already taken
    finally:
        conn.close()


def get_user(username: str, password: str) -> dict | None:
    """
    Verify credentials. Returns the user row if valid, else None.
    Uses werkzeug's safe hash comparison to prevent timing attacks.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username.strip(),)
    ).fetchone()
    conn.close()
    if row and check_password_hash(row["password_hash"], password):
        return dict(row)
    return None


# ---------------------------------------------------------------------------
# API Key Vault (Multi-Provider BYOK)
# ---------------------------------------------------------------------------

def save_provider_key(user_id: int, provider: str, encrypted_blob: bytes) -> None:
    """
    Upsert an encrypted API key for a specific provider.
    Uses INSERT OR REPLACE on the (user_id, provider) unique constraint.
    """
    conn = _get_conn()
    with conn:
        conn.execute(
            """INSERT INTO api_keys (user_id, provider, encrypted_key)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, provider)
               DO UPDATE SET encrypted_key = excluded.encrypted_key,
                             created_at = CURRENT_TIMESTAMP""",
            (user_id, provider.lower(), encrypted_blob),
        )
    conn.close()


def get_provider_key(user_id: int, provider: str) -> bytes | None:
    """Fetch the encrypted API key blob for a specific provider."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT encrypted_key FROM api_keys WHERE user_id = ? AND provider = ?",
        (user_id, provider.lower()),
    ).fetchone()
    conn.close()
    return bytes(row["encrypted_key"]) if row and row["encrypted_key"] else None


def get_all_provider_keys(user_id: int) -> list[dict]:
    """Return all stored provider entries for a user (provider name + key exists flag)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT provider, created_at FROM api_keys WHERE user_id = ? ORDER BY provider",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_provider_key(user_id: int, provider: str) -> None:
    """Remove a specific provider's API key."""
    conn = _get_conn()
    with conn:
        conn.execute(
            "DELETE FROM api_keys WHERE user_id = ? AND provider = ?",
            (user_id, provider.lower()),
        )
    conn.close()


# Legacy compatibility — kept for backward compat but no longer the primary path
def save_api_key(user_id: int, encrypted_blob: bytes) -> None:
    """Legacy: save to users.encrypted_api_key column."""
    conn = _get_conn()
    with conn:
        conn.execute(
            "UPDATE users SET encrypted_api_key = ? WHERE user_id = ?",
            (encrypted_blob, user_id),
        )
    conn.close()


def get_encrypted_api_key(user_id: int) -> bytes | None:
    """Legacy: fetch from users.encrypted_api_key column."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT encrypted_api_key FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return bytes(row["encrypted_api_key"]) if row and row["encrypted_api_key"] else None


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------

def create_bot(user_id: int, bot_name: str, system_prompt: str,
               provider: str = "google", model_name: str = "gemini-2.5-flash") -> dict:
    """Insert a new bot and return its row."""
    conn = _get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO bots (user_id, bot_name, system_prompt, provider, model_name) VALUES (?, ?, ?, ?, ?)",
            (user_id, bot_name.strip(), system_prompt.strip(), provider.lower(), model_name),
        )
        bot_id = cur.lastrowid
    row = conn.execute("SELECT * FROM bots WHERE bot_id = ?", (bot_id,)).fetchone()
    conn.close()
    return dict(row)


def get_bots_for_user(user_id: int) -> list[dict]:
    """Return all bots owned by a user, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bots WHERE user_id = ? ORDER BY bot_id DESC", (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot_by_id(bot_id: int) -> dict | None:
    """Return a single bot row or None."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM bots WHERE bot_id = ?", (bot_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_bot(bot_id: int, bot_name: str, system_prompt: str,
               provider: str | None = None, model_name: str | None = None) -> None:
    """Update a bot's name, system prompt, and optionally its provider/model."""
    conn = _get_conn()
    with conn:
        if provider is not None and model_name is not None:
            conn.execute(
                "UPDATE bots SET bot_name = ?, system_prompt = ?, provider = ?, model_name = ? WHERE bot_id = ?",
                (bot_name.strip(), system_prompt.strip(), provider.lower(), model_name, bot_id),
            )
        else:
            conn.execute(
                "UPDATE bots SET bot_name = ?, system_prompt = ? WHERE bot_id = ?",
                (bot_name.strip(), system_prompt.strip(), bot_id),
            )
    conn.close()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def add_document(bot_id: int, filename: str) -> dict:
    """Insert a document record with status='pending' and return the row."""
    conn = _get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO documents (bot_id, filename, status) VALUES (?, ?, 'pending')",
            (bot_id, filename),
        )
        doc_id = cur.lastrowid
    row = conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    conn.close()
    return dict(row)


def get_documents_for_bot(bot_id: int) -> list[dict]:
    """Return all documents for a given bot, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM documents WHERE bot_id = ? ORDER BY doc_id DESC", (bot_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_document_status(doc_id: int, status: str, page_count: int | None = None) -> None:
    """Update a document's processing status and optionally its page count."""
    conn = _get_conn()
    with conn:
        if page_count is not None:
            conn.execute(
                "UPDATE documents SET status = ?, page_count = ? WHERE doc_id = ?",
                (status, page_count, doc_id),
            )
        else:
            conn.execute(
                "UPDATE documents SET status = ? WHERE doc_id = ?",
                (status, doc_id),
            )
    conn.close()


def delete_document(doc_id: int) -> None:
    """Delete a document record from SQLite. Call after cleaning ChromaDB and filesystem."""
    conn = _get_conn()
    with conn:
        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
    conn.close()


def get_all_doc_ids_for_bot(bot_id: int) -> list[int]:
    """Return just the doc_id list for a bot — used during bot deletion to clean RAG data."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT doc_id FROM documents WHERE bot_id = ?", (bot_id,)
    ).fetchall()
    conn.close()
    return [row["doc_id"] for row in rows]


def delete_bot(bot_id: int) -> None:
    """
    Delete a bot and all its documents from SQLite.
    The ON DELETE CASCADE on the documents table handles child rows automatically.
    Caller must clean ChromaDB and filesystem BEFORE calling this.
    """
    conn = _get_conn()
    with conn:
        conn.execute("DELETE FROM bots WHERE bot_id = ?", (bot_id,))
    conn.close()

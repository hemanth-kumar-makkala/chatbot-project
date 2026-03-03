"""
database.py — SQLite schema initialization and CRUD helpers.
All DB operations are collected here so the rest of the app stays clean.
"""
import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "chatbot.db"


def _get_conn() -> sqlite3.Connection:
    """Open (or reuse) a SQLite connection with row-factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they don't already exist."""
    conn = _get_conn()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    NOT NULL UNIQUE,
                password TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name          TEXT    NOT NULL,
                system_prompt TEXT    NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS documents (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id   INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
                filename TEXT    NOT NULL,
                status   TEXT    NOT NULL DEFAULT 'pending'
            );
        """)
    conn.close()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def register_user(username: str, password: str) -> dict | None:
    """
    Insert a new user. Returns the new user row dict on success,
    or None if the username already exists.
    NOTE: passwords are stored plain-text (local dev only).
    """
    conn = _get_conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (username.strip(), password),
            )
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip(),)
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_user(username: str, password: str) -> dict | None:
    """Return the user row if credentials match, else None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username.strip(), password),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------

def create_bot(creator_id: int, name: str, system_prompt: str) -> dict:
    """Insert a new bot and return its row."""
    conn = _get_conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO bots (creator_id, name, system_prompt) VALUES (?, ?, ?)",
            (creator_id, name.strip(), system_prompt.strip()),
        )
        bot_id = cur.lastrowid
    row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    conn.close()
    return dict(row)


def get_bots_for_user(user_id: int) -> list[dict]:
    """Return all bots owned by a user."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bots WHERE creator_id = ? ORDER BY id DESC", (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot_by_id(bot_id: int) -> dict | None:
    """Return a single bot row or None."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_bot(bot_id: int, name: str, system_prompt: str) -> None:
    """Update a bot's name and system prompt."""
    conn = _get_conn()
    with conn:
        conn.execute(
            "UPDATE bots SET name = ?, system_prompt = ? WHERE id = ?",
            (name.strip(), system_prompt.strip(), bot_id),
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
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.close()
    return dict(row)


def get_documents_for_bot(bot_id: int) -> list[dict]:
    """Return all documents for a given bot."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM documents WHERE bot_id = ? ORDER BY id DESC", (bot_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_document_status(doc_id: int, status: str) -> None:
    """Update a document's processing status (e.g., 'processed' or 'error')."""
    conn = _get_conn()
    with conn:
        conn.execute(
            "UPDATE documents SET status = ? WHERE id = ?", (status, doc_id)
        )
    conn.close()

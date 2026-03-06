"""
encryption.py — Fernet AES-256 symmetric encryption for BYOK API keys.

The MASTER_KEY lives strictly in .env and never touches the database.
All user API keys are encrypted before storage and decrypted only at
the moment of an LLM API call.

Usage:
    from encryption import encrypt_key, decrypt_key

    blob = encrypt_key("sk-abc123...")         # store this in SQLite
    raw  = decrypt_key(blob)                   # use this to call the LLM API
"""
import os
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()


def _get_fernet() -> Fernet:
    """Load the master key from .env and return a ready Fernet instance."""
    master_key = os.environ.get("MASTER_KEY")
    if not master_key:
        raise EnvironmentError(
            "MASTER_KEY is not set in your .env file. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(master_key.encode())


def encrypt_key(raw_api_key: str) -> bytes:
    """
    Encrypt a raw API key string and return an encrypted byte blob.
    This blob is safe to store in SQLite.

    Args:
        raw_api_key: The user's plain-text API key (e.g. "AIza...", "sk-...")

    Returns:
        Encrypted bytes to store in the database.

    Raises:
        EnvironmentError: If MASTER_KEY is missing from .env
    """
    f = _get_fernet()
    return f.encrypt(raw_api_key.strip().encode())


def decrypt_key(encrypted_blob: bytes) -> str:
    """
    Decrypt a stored encrypted blob back into the raw API key string.
    Call this only immediately before making an LLM API call.

    Args:
        encrypted_blob: The bytes stored in the database.

    Returns:
        The raw API key string.

    Raises:
        EnvironmentError:  If MASTER_KEY is missing from .env
        ValueError:        If the blob is corrupted or the master key has changed.
    """
    f = _get_fernet()
    try:
        return f.decrypt(encrypted_blob).decode()
    except InvalidToken as exc:
        raise ValueError(
            "Failed to decrypt API key. The MASTER_KEY may have changed or "
            "the stored key is corrupted. The user must re-enter their API key."
        ) from exc

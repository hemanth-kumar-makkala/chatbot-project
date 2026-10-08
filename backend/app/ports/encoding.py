"""Text encoding helpers for ports and adapters (Task 2.2, 2.6).

Pure standard-library implementation with zero vendor dependencies.
"""

from __future__ import annotations

from app.ports.errors import FileValidationError


def decode_text_content(content: bytes) -> str:
    """Decode text bytes using UTF-8 (handling UTF-8 BOM) or Windows-1252 fallback.

    Raises:
        FileValidationError: If content cannot be decoded or contains NUL bytes.
    """
    raw_text_bytes = content[3:] if content.startswith(b"\xef\xbb\xbf") else content
    try:
        text = raw_text_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw_text_bytes.decode("cp1252")
        except UnicodeDecodeError as exc:
            msg = "Unsupported text encoding or unparseable text content"
            raise FileValidationError(msg) from exc

    if "\x00" in text:
        raise FileValidationError("Text file contains NUL bytes")
    return text

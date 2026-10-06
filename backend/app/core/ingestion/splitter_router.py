"""Document splitting routing engine (Task 2.3).

Routes documents based on extension:
- Paged documents (.pdf, .tiff, .tif): Require page splitting and rendering.
- Non-paged documents (.txt, .csv, .xlsx): Skip splitting; chunked directly.
"""

from __future__ import annotations

_PAGED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".tiff", ".tif"})


def should_split_document(extension: str) -> bool:
    """Determine whether a document requires page splitting based on file extension."""
    clean_ext = extension.lower().strip()
    if not clean_ext.startswith("."):
        clean_ext = f".{clean_ext}"
    return clean_ext in _PAGED_EXTENSIONS

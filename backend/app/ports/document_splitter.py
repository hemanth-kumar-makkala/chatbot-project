"""Port interface for DocumentSplitter.

Extracts pages from multi-page document containers (PDF, TIFF).
Splits document into individual page artifacts and classifies each page into
one of: 'digital_text', 'scanned', 'image_heavy', 'blank'.
"""

from abc import ABC, abstractmethod

from app.ports.types import PageArtifact


class DocumentSplitter(ABC):
    """Abstract port for splitting multi-page documents and rendering page images."""

    @abstractmethod
    async def split(
        self,
        content: bytes,
        extension: str,
        max_pages: int = 500,
    ) -> list[PageArtifact]:
        """Split document into individual classified page artifacts.

        Raises:
            EncryptedDocumentError: If document is password protected.
            CorruptDocumentError: If document stream is corrupted or unparseable.
            DecompressionBombError: If page dimensions or pixels exceed safety limits.
            PageLimitExceededError: If page count exceeds max_pages.
        """
        ...

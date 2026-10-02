"""Port interface for DocumentParser.

Enforces the page-first invariant:
Documents are split into individual page artifacts first, and text/tables/images
are extracted page by page. Never parses whole files into a single monolithic block.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.ports.types import PageArtifact, ParsedPageContent


class DocumentParser(ABC):
    """Abstract port for document parsing and OCR."""

    @abstractmethod
    def split_pages(
        self,
        file: bytes,
        filename: str,
    ) -> AsyncIterator[PageArtifact]:
        """Split a document into individual page artifacts as an async stream."""
        ...

    @abstractmethod
    async def extract(
        self,
        page: PageArtifact,
    ) -> ParsedPageContent:
        """Extract text, tables, and confidence from an individual page."""
        ...

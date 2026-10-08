"""Mock DocumentParser adapter for testing and environments without docling."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.ports.document_parser import DocumentParser
from app.ports.types import PageArtifact, PageType, ParsedPageContent, TableData


class MockDocumentParser(DocumentParser):
    """Deterministic in-memory mock of DocumentParser port."""

    def __init__(
        self,
        default_confidence: float = 0.95,
        default_text: str = "Mock extracted content",
        default_tables: list[TableData] | None = None,
    ) -> None:
        self.default_confidence = default_confidence
        self.default_text = default_text
        self.default_tables = default_tables or []

    async def split_pages(
        self,
        file: bytes,
        filename: str,
    ) -> AsyncIterator[PageArtifact]:
        """Split pages using synthetic or lightweight splitter."""
        from app.adapters.pypdfium_splitter import PyPdfiumDocumentSplitter

        splitter = PyPdfiumDocumentSplitter()
        extension = filename.rsplit(".", 1)[-1] if "." in filename else "pdf"
        pages = await splitter.split(file, extension)
        for page in pages:
            yield page

    async def extract(
        self,
        page: PageArtifact,
    ) -> ParsedPageContent:
        """Return deterministic parsed content based on page type."""
        conf = 1.0 if page.page_type == PageType.DIGITAL_TEXT else self.default_confidence
        return ParsedPageContent(
            page_number=page.page_number,
            text=f"{self.default_text} for page {page.page_number}",
            tables=self.default_tables,
            confidence=conf,
        )

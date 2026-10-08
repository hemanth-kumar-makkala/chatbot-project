"""File validation and content sniffing service (Task 2.2).

Inspects file contents via magic byte analysis, structural verification,
encoding checks, size limit enforcement, and malware scanning.
Zero PHI in logs and error messages.
"""

import hashlib
import io
import zipfile

from app.ports.errors import FileValidationError, MalwareDetectedError
from app.ports.malware_scanner import MalwareScanner
from app.ports.types import ValidatedFile

MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MB
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
        "text/plain",
    }
)

# Shared text encoding helper from ports (re-exported for backwards compatibility)
from app.ports.encoding import decode_text_content  # noqa: E402


def _sniff_and_validate(content: bytes, filename: str) -> tuple[str, str]:
    """Sniff MIME type and extension from raw content bytes."""
    if len(content) == 0:
        raise FileValidationError("File is empty")

    if len(content) > MAX_FILE_SIZE_BYTES:
        raise FileValidationError("File exceeds maximum allowed size")

    # Reject known binary executable headers immediately
    if content.startswith(b"MZ") or content.startswith(b"\x7fELF"):
        raise FileValidationError("Binary executable payload is not permitted")

    # 1. PDF
    if content.startswith(b"%PDF-"):
        return "application/pdf", "pdf"

    # 2. PNG
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"

    # 3. JPEG
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpeg"

    # 4. TIFF
    if content.startswith(b"II*\x00") or content.startswith(b"MM\x00*"):
        return "image/tiff", "tiff"

    # 5. XLSX (ZIP archive structure check)
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                infos = zf.infolist()
                if len(infos) > 500:
                    raise FileValidationError("Zip archive exceeds maximum entry limit")

                uncompressed = sum(info.file_size for info in infos)
                if uncompressed > MAX_FILE_SIZE_BYTES:
                    raise FileValidationError("Zip archive uncompressed size exceeds limit")

                if len(content) > 0 and (uncompressed / len(content)) > 100:
                    raise FileValidationError("Zip archive compression ratio exceeds limit")

                if "[Content_Types].xml" not in zf.namelist():
                    raise FileValidationError("Invalid or unsupported Office open XML container")

                content_types = zf.read("[Content_Types].xml").decode("utf-8", errors="replace")
                if (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml"
                    not in content_types
                ):
                    raise FileValidationError("Archive does not contain valid spreadsheet content")

                if "wordprocessingml" in content_types or "macroEnabled" in content_types:
                    raise FileValidationError(
                        "Word documents and macro-enabled workbooks are not permitted"
                    )

            return (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xlsx",
            )
        except zipfile.BadZipFile as exc:
            raise FileValidationError("Corrupted or malformed zip archive") from exc

    # 6. CSV / Plain Text
    text_content: str | None = None
    try:
        text_content = decode_text_content(content)
    except FileValidationError:
        text_content = None

    if text_content is not None:
        # Reject binary data with control characters (< 32, excluding \t, \n, \r)
        control_chars = sum(1 for c in text_content if ord(c) < 32 and c not in ("\t", "\n", "\r"))
        if control_chars > 0:
            raise FileValidationError("Corrupted or binary control characters in text file")

        if len(text_content.strip()) == 0:
            raise FileValidationError("Text file is empty")

        is_csv_target = filename.lower().endswith(".csv")
        is_txt_target = filename.lower().endswith(".txt")
        has_delimiter = "," in text_content or "\t" in text_content or ";" in text_content

        if is_csv_target:
            if not has_delimiter:
                raise FileValidationError("CSV file does not contain valid tabular delimiters")
            return "text/csv", "csv"

        if is_txt_target:
            return "text/plain", "txt"

        if has_delimiter:
            return "text/csv", "csv"

        raise FileValidationError("Unsupported file format or unparseable content")

    raise FileValidationError("Unsupported file format or unparseable content")


async def validate_file(
    filename: str,
    content: bytes,
    malware_scanner: MalwareScanner | None = None,
) -> ValidatedFile:
    """Validate file content, sniff real MIME type, enforce security limits, and scan for malware.

    Raises:
        FileValidationError: If format is unsupported, headers mismatch, text is corrupted/binary,
                            or file exceeds size limits.
        MalwareDetectedError: If malware scanner reports unsafe payload.
    """
    if len(content) == 0:
        raise FileValidationError("File is empty")

    if len(content) > MAX_FILE_SIZE_BYTES:
        raise FileValidationError("File exceeds maximum allowed size")

    if malware_scanner is not None:
        is_clean = await malware_scanner.scan(content)
        if not is_clean:
            raise MalwareDetectedError("Malware detected in payload")

    mime_type, extension = _sniff_and_validate(content, filename)

    sha256 = hashlib.sha256(content).hexdigest()

    return ValidatedFile(
        mime_type=mime_type,
        extension=extension,
        sha256=sha256,
        size_bytes=len(content),
        content=content,
    )

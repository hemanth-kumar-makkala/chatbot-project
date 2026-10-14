"""Unit tests for document upload validation, file sniffing, and wiring guards (Task 2.2)."""

import io
import os
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from app.adapters.parsing.docling import AcceleratorDevice, DoclingDocumentParser
from app.adapters.parsing.mock import MockDocumentParser
from app.core.ingestion.validation import validate_file
from app.ports.errors import (
    ConfigurationError,
    FileValidationError,
    MalwareDetectedError,
)
from app.ports.types import StorageKey
from app.wiring import (
    create_document_parser,
    create_malware_scanner,
    create_storage_backend,
)


# --- Helpers for creating valid synthetic file payloads in memory ---
def _create_minimal_valid_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            "</Types>"
        )
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("xl/workbook.xml", "<workbook/>")
    return buf.getvalue()


def _create_minimal_docx_as_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        )
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def _create_macro_xlsm_as_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/>'
            "</Types>"
        )
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("xl/workbook.xml", "<workbook/>")
    return buf.getvalue()


# --- Test Cases ---


@pytest.mark.asyncio
async def test_validate_valid_pdf() -> None:
    content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    res = await validate_file("invoice.pdf", content)
    assert res.mime_type == "application/pdf"
    assert res.extension == "pdf"
    assert res.size_bytes == len(content)


@pytest.mark.asyncio
async def test_validate_valid_png() -> None:
    content = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    )
    res = await validate_file("diagram.png", content)
    assert res.mime_type == "image/png"
    assert res.extension == "png"


@pytest.mark.asyncio
async def test_validate_valid_jpeg() -> None:
    content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xd9"
    res = await validate_file("photo.jpeg", content)
    assert res.mime_type == "image/jpeg"
    assert res.extension == "jpeg"


@pytest.mark.asyncio
async def test_validate_valid_tiff() -> None:
    content = b"II*\x00\x08\x00\x00\x00\x00\x00"
    res = await validate_file("scan.tiff", content)
    assert res.mime_type == "image/tiff"
    assert res.extension == "tiff"


@pytest.mark.asyncio
async def test_validate_valid_xlsx() -> None:
    content = _create_minimal_valid_xlsx()
    res = await validate_file("financials.xlsx", content)
    assert res.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert res.extension == "xlsx"


@pytest.mark.asyncio
async def test_validate_valid_csv_utf8() -> None:
    content = b"patient_id,dose_mg,administered_at\nP101,50.0,2026-10-01\n"
    res = await validate_file("records.csv", content)
    assert res.mime_type == "text/csv"
    assert res.extension == "csv"


@pytest.mark.asyncio
async def test_validate_accepts_real_cp1252_csv() -> None:
    # Windows-1252 with accented character (René), smart quotes (“ ”), and euro symbol (€)
    text = "id,name,quote,price\n1,René,“Smart quote”,€150\n"
    content = text.encode("cp1252")
    res = await validate_file("records_cp1252.csv", content)
    assert res.mime_type == "text/csv"
    assert res.extension == "csv"


@pytest.mark.asyncio
async def test_validate_accepts_utf8_bom_csv() -> None:
    content = b"\xef\xbb\xbfid,name\n1,Alice\n2,Bob\n"
    res = await validate_file("bom.csv", content)
    assert res.mime_type == "text/csv"
    assert res.extension == "csv"


@pytest.mark.asyncio
async def test_validate_accepts_plain_text_without_delimiters() -> None:
    content = b"This is a normal paragraph with standard sentences.\nIt has no commas or tabs."
    res = await validate_file("notes.txt", content)
    assert res.mime_type == "text/plain"
    assert res.extension == "txt"


@pytest.mark.asyncio
async def test_validate_rejects_cp1252_random_binary_named_csv() -> None:
    # Fixed deterministic binary bytes named data.csv must be rejected
    deterministic_binary = bytes(range(256)) * 4
    with pytest.raises(FileValidationError):
        await validate_file("data.csv", deterministic_binary)


@pytest.mark.asyncio
async def test_validate_rejects_nul_bytes_in_csv() -> None:
    corrupted_csv = b"col1,col2\nval1\x00,val2\n"
    with pytest.raises(FileValidationError):
        await validate_file("records.csv", corrupted_csv)


@pytest.mark.asyncio
async def test_validate_rejects_docx_disguised_as_xlsx() -> None:
    content = _create_minimal_docx_as_xlsx()
    with pytest.raises(FileValidationError):
        await validate_file("disguised.xlsx", content)


@pytest.mark.asyncio
async def test_validate_rejects_xlsm_macro_disguised_as_xlsx() -> None:
    content = _create_macro_xlsm_as_xlsx()
    with pytest.raises(FileValidationError):
        await validate_file("macro.xlsx", content)


@pytest.mark.asyncio
async def test_validate_rejects_arbitrary_zip_named_xlsx() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.txt", "hello world")
    with pytest.raises(FileValidationError):
        await validate_file("test.xlsx", buf.getvalue())


@pytest.mark.asyncio
async def test_validate_rejects_zip_bomb_excessive_entries() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            "</Types>",
        )
        for i in range(505):
            zf.writestr(f"xl/sheet{i}.xml", "<sheet/>")
    with pytest.raises(FileValidationError):
        await validate_file("bomb.xlsx", buf.getvalue())


@pytest.mark.asyncio
async def test_validate_rejects_executable_disguised_as_pdf() -> None:
    # MZ DOS / Windows executable header
    exe_content = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + (b"\x00" * 500)
    with pytest.raises(FileValidationError):
        await validate_file("malicious.pdf", exe_content)


@pytest.mark.asyncio
async def test_validate_rejects_empty_file() -> None:
    with pytest.raises(FileValidationError):
        await validate_file("empty.pdf", b"")


@pytest.mark.asyncio
async def test_validate_rejects_oversized_file() -> None:
    oversized = b"A" * (50 * 1024 * 1024 + 1)
    with pytest.raises(FileValidationError):
        await validate_file("huge.csv", oversized)


@pytest.mark.asyncio
async def test_validate_malware_detection_raises_malware_detected_error() -> None:
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    scanner = create_malware_scanner("mock")
    with pytest.raises(MalwareDetectedError):
        await validate_file("test_eicar.pdf", eicar, malware_scanner=scanner)


@pytest.mark.asyncio
async def test_validate_file_exactly_at_size_limit_accepted() -> None:
    exact_size = 50 * 1024 * 1024
    header = b"col1,col2\n"
    line = b"a,b\n"
    num_lines = (exact_size - len(header)) // len(line)
    remainder = exact_size - len(header) - (num_lines * len(line))
    content = header + (line * num_lines)
    if remainder > 0:
        content += (b"x" * (remainder - 1)) + b"\n"
    assert len(content) == exact_size
    res = await validate_file("limit.csv", content)
    assert res.size_bytes == exact_size
    assert res.mime_type == "text/csv"


@pytest.mark.asyncio
async def test_validate_clean_file_passes_mock_scanner() -> None:
    clean_pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    scanner = create_malware_scanner("mock")
    res = await validate_file("clean.pdf", clean_pdf, malware_scanner=scanner)
    assert res.mime_type == "application/pdf"
    assert res.extension == "pdf"


@pytest.mark.asyncio
async def test_validate_error_never_contains_phi_or_filename() -> None:
    sensitive_filename = "JaneDoe_CancerDiagnosis_2026.pdf"
    corrupt_content = b"INVALID_GARBAGE_BYTES_123"
    with pytest.raises(FileValidationError) as exc_info:
        await validate_file(sensitive_filename, corrupt_content)

    err_str = str(exc_info.value)
    assert sensitive_filename not in err_str
    assert "JaneDoe" not in err_str
    assert "CancerDiagnosis" not in err_str


def test_malware_wiring_guard_profile_allowlist() -> None:
    # Synthetic local profiles succeed
    for profile in ["mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api"]:
        scanner = create_malware_scanner(profile)
        assert scanner is not None

    # Cloud, frontier, or unknown profiles MUST refuse to start
    with pytest.raises(ConfigurationError):
        create_malware_scanner("cloud")

    with pytest.raises(ConfigurationError):
        create_malware_scanner("frontier")

    with pytest.raises(ConfigurationError):
        create_malware_scanner("some-unknown-profile")


def test_storage_backend_wiring_guard_profile_allowlist() -> None:
    # Synthetic local profiles succeed
    for profile in ["mock", "office-cpu", "desktop-gpu", "home-gpu", "dev-free-api"]:
        storage = create_storage_backend(profile)
        assert storage is not None

    # Cloud, frontier, or unknown profiles MUST refuse to start
    with pytest.raises(ConfigurationError):
        create_storage_backend("cloud")

    with pytest.raises(ConfigurationError):
        create_storage_backend("frontier")

    with pytest.raises(ConfigurationError):
        create_storage_backend("some-unknown-profile")


def test_document_parser_wiring_guard_profile_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 1. Row 'mock': returns MockDocumentParser
    mock_parser = create_document_parser("mock")
    assert isinstance(mock_parser, MockDocumentParser)

    # 2. Rows 'office-cpu', 'desktop-gpu', 'home-gpu', 'dev-free-api':
    # require DOCLING_ARTIFACTS_PATH to return real DoclingDocumentParser
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(models_dir))

    for profile in ["office-cpu", "desktop-gpu", "home-gpu", "dev-free-api"]:
        parser = create_document_parser(profile)
        assert isinstance(parser, DoclingDocumentParser)
        assert not isinstance(parser, MockDocumentParser)
        assert parser.artifacts_path == models_dir
        expected_device = (
            AcceleratorDevice.CUDA
            if profile in ("desktop-gpu", "home-gpu")
            else AcceleratorDevice.CPU
        )
        assert parser.device == expected_device

    # 3. Row 'cloud' with DOCLING_ARTIFACTS_PATH: returns DoclingDocumentParser
    cloud_parser = create_document_parser("cloud")
    assert isinstance(cloud_parser, DoclingDocumentParser)
    assert not isinstance(cloud_parser, MockDocumentParser)
    assert cloud_parser.artifacts_path == models_dir
    assert cloud_parser.device == AcceleratorDevice.CPU

    # Row 'cloud' without DOCLING_ARTIFACTS_PATH: raises ConfigurationError
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    with pytest.raises(
        ConfigurationError,
        match="DOCLING_ARTIFACTS_PATH",
    ):
        create_document_parser("cloud")

    # 4. Row unknown profile: raises ConfigurationError
    for unknown in ["frontier", "unknown-profile"]:
        with pytest.raises(ConfigurationError):
            create_document_parser(unknown)


def test_docling_adapter_applies_offline_and_pinned_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validate that DoclingDocumentParser receives and applies offline/artifacts_path settings.

    Tests that:
    - artifacts_path is applied to DoclingDocumentParser.
    - HF_HUB_OFFLINE environment variable is set to '1'.
    - Inspects configured options without triggering real model weight downloads.
    """
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    # 1. Cloud wiring passes artifacts_path from DOCLING_ARTIFACTS_PATH
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(models_dir))

    cloud_parser = create_document_parser("cloud")
    assert isinstance(cloud_parser, DoclingDocumentParser)
    assert cloud_parser.artifacts_path == models_dir
    assert cloud_parser.device == AcceleratorDevice.CPU

    # 2. Inspect adapter configured options without loading any models
    opts = cloud_parser.get_configured_options()
    assert opts["artifacts_path"] == models_dir
    assert opts["device"] == AcceleratorDevice.CPU
    assert opts["hf_hub_offline"] == "1"
    assert "HF_HUB_OFFLINE" in os.environ and os.environ["HF_HUB_OFFLINE"] == "1"

    # 3. Direct instantiation requires typed artifacts_path: Path and device: AcceleratorDevice
    custom_dir = tmp_path / "custom_models"
    custom_dir.mkdir()
    direct_parser = DoclingDocumentParser(
        artifacts_path=custom_dir,
        device=AcceleratorDevice.CPU,
    )
    direct_opts = direct_parser.get_configured_options()
    assert direct_opts["artifacts_path"] == custom_dir
    assert direct_opts["device"] == AcceleratorDevice.CPU
    assert direct_opts["hf_hub_offline"] == "1"

    # 4. Local synthetic profile wiring also applies artifacts_path and sets offline
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    local_parser = create_document_parser("dev-free-api")
    assert isinstance(local_parser, DoclingDocumentParser)
    assert local_parser.device == AcceleratorDevice.CPU
    local_opts = local_parser.get_configured_options()
    assert local_opts["artifacts_path"] == models_dir
    assert local_opts["device"] == AcceleratorDevice.CPU
    assert local_opts["hf_hub_offline"] == "1"


def test_storage_key_path_traversal_sanitization() -> None:
    tenant_id = uuid4()

    with pytest.raises(ValueError, match="\\.\\."):
        StorageKey(tenant_id=tenant_id, path="../etc/passwd")

    with pytest.raises(ValueError, match="backslashes"):
        StorageKey(tenant_id=tenant_id, path="documents\\evil.pdf")

    with pytest.raises(ValueError, match="leading slash"):
        StorageKey(tenant_id=tenant_id, path="/documents/evil.pdf")

    with pytest.raises(ValueError, match="double slashes"):
        StorageKey(tenant_id=tenant_id, path="documents//evil.pdf")

    valid = StorageKey(tenant_id=tenant_id, path="documents/valid.pdf")
    assert valid.full_key == f"{tenant_id}/documents/valid.pdf"


def test_wiring_factories_require_profile_argument_without_default() -> None:
    from unittest.mock import MagicMock

    from app.wiring import create_upload_service

    with pytest.raises(TypeError):
        create_malware_scanner()  # type: ignore[call-arg]

    empty_profile_args: tuple[str, ...] = ()
    with pytest.raises(TypeError):
        create_document_parser(*empty_profile_args)

    with pytest.raises(TypeError):
        create_storage_backend()  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        create_upload_service(session=MagicMock())  # type: ignore[call-arg]

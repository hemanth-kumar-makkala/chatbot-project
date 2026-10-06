"""Synthetic document generators for test suite.

Generates 100% synthetic, in-memory PDF and TIFF fixtures for unit and integration testing.
Zero real documents, zero PHI, zero binary files committed to git.
"""

from __future__ import annotations

import io

import pypdf
from PIL import Image


def _create_single_page_text_pdf(text: str) -> bytes:
    """Generate a single-page PDF with exact xref table containing raw digital text."""
    escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream_data = f"BT /F1 12 Tf 72 712 Td ({escaped_text}) Tj ET".encode("latin-1")
    stream_len = len(stream_data)

    header = b"%PDF-1.4\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    obj4 = (
        f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("latin-1")
        + stream_data
        + b"\nendstream\nendobj\n"
    )
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    offsets = [0]
    pos = len(header)
    for obj in [obj1, obj2, obj3, obj4, obj5]:
        offsets.append(pos)
        pos += len(obj)

    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets[1:]:
        xref += f"{off:010d} 00000 n \n".encode("latin-1")

    trailer = f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{pos}\n%%EOF\n".encode("latin-1")
    return header + obj1 + obj2 + obj3 + obj4 + obj5 + xref + trailer


def create_synthetic_blank_pdf(num_pages: int = 1) -> bytes:
    """Generate a valid, multi-page or single-page blank PDF (zero characters, zero images)."""
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_synthetic_digital_pdf(
    text: str = "Synthetic clinical guidelines for hypertension.",
    num_pages: int = 1,
) -> bytes:
    """Generate a PDF containing selectable digital text and zero images."""
    single_pdf = _create_single_page_text_pdf(text)
    if num_pages == 1:
        return single_pdf

    reader = pypdf.PdfReader(io.BytesIO(single_pdf))
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        writer.add_page(reader.pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_synthetic_scanned_pdf(
    width: int = 612,
    height: int = 792,
    num_pages: int = 1,
) -> bytes:
    """Generate a scanned PDF with full-page raster images and zero digital text."""
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        img = Image.new("RGB", (width, height), color=(240, 240, 240))
        img_buf = io.BytesIO()
        img.save(img_buf, format="PDF")
        reader = pypdf.PdfReader(img_buf)
        writer.add_page(reader.pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_synthetic_searchable_scanned_pdf(
    text: str = "Searchable OCR text layer behind raster scan",
) -> bytes:
    """Generate a searchable scanned PDF: full-page background raster image + digital text."""
    # 1. Full-page raster scan
    img = Image.new("RGB", (612, 792), color=(235, 235, 235))
    img_buf = io.BytesIO()
    img.save(img_buf, format="PDF")
    img_reader = pypdf.PdfReader(img_buf)

    # 2. Digital text layer
    text_pdf = _create_single_page_text_pdf(text)
    text_reader = pypdf.PdfReader(io.BytesIO(text_pdf))

    writer = pypdf.PdfWriter()
    page = img_reader.pages[0]
    page.merge_page(text_reader.pages[0])
    writer.add_page(page)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_synthetic_image_heavy_pdf(
    text: str = "Mixed document containing diagram and digital narrative.",
) -> bytes:
    """Generate an image-heavy PDF: substantial graphic/image (~45% page area) + text."""
    # Create image covering partial page (approx 45% area: 400x450 on 612x792)
    img = Image.new("RGB", (400, 450), color=(180, 200, 220))
    img_buf = io.BytesIO()
    img.save(img_buf, format="PDF")
    img_reader = pypdf.PdfReader(img_buf)

    text_pdf = _create_single_page_text_pdf(text)
    text_reader = pypdf.PdfReader(io.BytesIO(text_pdf))

    writer = pypdf.PdfWriter()
    page = text_reader.pages[0]
    page.merge_page(img_reader.pages[0])
    writer.add_page(page)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_synthetic_encrypted_pdf(password: str = "secret_pw") -> bytes:
    """Generate a password-encrypted PDF using pypdf."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password=password, owner_password=password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_synthetic_corrupt_pdf() -> bytes:
    """Generate a corrupted byte stream with invalid PDF structure."""
    return b"%PDF-1.4\n\x00\x01\x02Corrupted unparseable PDF stream data%%EOF"


def create_synthetic_oversized_pdf(width_pts: int = 6000, height_pts: int = 6000) -> bytes:
    """Generate an oversized PDF page exceeding 5000x5000 pt dimension bounds."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=width_pts, height=height_pts)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_synthetic_multipage_pdf(num_pages: int = 501) -> bytes:
    """Generate a PDF with specified page count (default 501 to test page limit)."""
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_synthetic_multipage_tiff(
    num_frames: int = 2,
    width: int = 200,
    height: int = 200,
) -> bytes:
    """Generate a multi-frame TIFF in memory."""
    frames = [
        Image.new("RGB", (width, height), color=(i * 60 % 255, 100, 150)) for i in range(num_frames)
    ]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def create_synthetic_decompression_bomb_tiff() -> bytes:
    """Generate a TIFF frame exceeding 50M pixels safety threshold."""
    # 7500 x 7000 = 52,500,000 pixels (> 50_000_000 threshold)
    # Using 1-bit mode ('1') or palette ('P') keeps in-memory size tiny (< 7 MB)
    img = Image.new("1", (7500, 7000), color=0)
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()

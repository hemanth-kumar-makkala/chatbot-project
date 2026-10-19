"""Render golden PDF pages to PNG thumbnails and full view images for demo UI.

Usage:
    python backend/scripts/render_demo_pages.py

Outputs:
    web/public/demo/pages/<doc>-p<N>.png        (view size, ~150 DPI)
    web/public/demo/pages/<doc>-p<N>-thumb.png  (thumbnail size, ~150px width)
    web/public/demo/docs/<doc>.pdf              (original golden PDF for source viewer)

Strictly processes golden documents from evals/golden/docs only. Never real documents.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image


def render_golden_demo_pages() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    golden_docs_dir = repo_root / "evals" / "golden" / "docs"
    if not golden_docs_dir.exists():
        raise FileNotFoundError(f"Golden docs directory not found at {golden_docs_dir}")

    web_public_dir = repo_root / "web" / "public"
    pages_dir = web_public_dir / "demo" / "pages"
    docs_dir = web_public_dir / "demo" / "docs"

    pages_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    # Collect all golden PDF files strictly inside evals/golden/docs
    pdf_files = sorted(golden_docs_dir.rglob("*.pdf"))
    if not pdf_files:
        raise RuntimeError(f"No PDF files found in {golden_docs_dir}")

    total_pages_rendered = 0
    total_docs_copied = 0

    print(f"--> Found {len(pdf_files)} golden PDFs in {golden_docs_dir}")

    for pdf_path in pdf_files:
        rel_path = pdf_path.relative_to(golden_docs_dir)
        # Determine stem prefixes
        # For tenant_a/patient_intake_form.pdf -> stems:
        # ['tenant_a_patient_intake_form', 'patient_intake_form']
        # For clean_fax.pdf -> stems: ['clean_fax']
        stem = pdf_path.stem
        stems = [stem]
        if len(rel_path.parts) > 1:
            prefix = "_".join(rel_path.parts[:-1])
            combined_stem = f"{prefix}_{stem}"
            if combined_stem not in stems:
                stems.append(combined_stem)

        print(f"--> Rendering {rel_path}...")

        # Copy original PDF to web/public/demo/docs/
        dest_pdf = docs_dir / pdf_path.name
        shutil.copy2(pdf_path, dest_pdf)
        total_docs_copied += 1
        if len(rel_path.parts) > 1:
            prefix = "_".join(rel_path.parts[:-1])
            comb_pdf = docs_dir / f"{prefix}_{pdf_path.name}"
            shutil.copy2(pdf_path, comb_pdf)

        pdf_doc = pdfium.PdfDocument(str(pdf_path))
        num_pages = len(pdf_doc)

        for page_idx in range(num_pages):
            page_num = page_idx + 1
            page = pdf_doc[page_idx]

            # Render view size at 150 DPI (scale = 150 / 72 ≈ 2.083)
            view_pil: Image.Image = page.render(scale=150.0 / 72.0).to_pil()

            # Generate small thumbnail (width = 150px, proportional height)
            w, h = view_pil.size
            thumb_w = 150
            thumb_h = max(1, int(h * (thumb_w / w)))
            thumb_pil: Image.Image = view_pil.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)

            for s in stems:
                view_target = pages_dir / f"{s}-p{page_num}.png"
                thumb_target = pages_dir / f"{s}-p{page_num}-thumb.png"

                view_pil.save(view_target, format="PNG")
                thumb_pil.save(thumb_target, format="PNG")

            total_pages_rendered += 1

    print(
        f"--> Finished rendering {total_pages_rendered} golden pages into {pages_dir} "
        f"and copied {total_docs_copied} PDFs to {docs_dir}."
    )


if __name__ == "__main__":
    render_golden_demo_pages()

"""
rag_engine.py — Hybrid Vision RAG pipeline.

INGEST: PDF  → PyMuPDF page images → Gemini Flash caption → ChromaDB embedding
        DOCX → python-docx paragraphs → Pillow text images → Gemini Flash caption → ChromaDB
QUERY:  User question → embed → ChromaDB top-3 pages → pass images to Gemini Vision
DELETE: Remove all ChromaDB nodes + page image folder for a document / entire bot

Resiliency:
  All external API calls are wrapped with tenacity exponential backoff (2→4→8→16→32s, 5 attempts)
  to survive transient rate-limit (429) errors without crashing ingestion.

Portability:
  Image paths stored in ChromaDB are relative to PROJECT_ROOT, resolved dynamically at query time.
"""
import os
import shutil
import textwrap
import traceback
from pathlib import Path
from dotenv import load_dotenv

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
import logging

from database import update_document_status
from vector_store import get_chroma_collection

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent
ASSETS_DIR = PROJECT_ROOT / "document_assets"
ASSETS_DIR.mkdir(exist_ok=True)

EMBED_MODEL = "models/gemini-embedding-001"
VISION_MODEL = "gemini-2.5-flash"
TOP_K = 3

SYSTEM_GUARDRAILS = (
    "You are a strict Data Extraction AI. "
    "Look at the provided document page images carefully. "
    "Extract and quote the exact text, table data, and numbers verbatim. "
    "Do not paraphrase or summarize unless explicitly asked. "
    "If the answer is ambiguous, ask the user for clarification. "
    "If the information is not present in the images, reply ONLY with: "
    "'Information not found in the uploaded documents.'"
)

# Logger for retry events
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------------------------
# Path helpers (portability)
# ---------------------------------------------------------------------------

def _to_relative_path(abs_path: Path) -> str:
    """Convert an absolute path to a relative path string from PROJECT_ROOT."""
    try:
        return str(abs_path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(abs_path)


def _to_absolute_path(rel_path: str) -> Path:
    """Resolve a stored relative path back to an absolute Path."""
    p = Path(rel_path)
    if p.is_absolute():
        return p  # Legacy absolute path — still works
    return PROJECT_ROOT / p


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _configure_genai(api_key: str) -> None:
    """Configure the google-generativeai SDK with the given key."""
    genai.configure(api_key=api_key)


def _get_page_folder(doc_id: int) -> Path:
    """Return (and create) the isolated folder for a document's page images."""
    folder = ASSETS_DIR / f"doc_{doc_id}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _render_docx_pages(docx_bytes: bytes, doc_id: int) -> list[Path]:
    """
    Convert a DOCX file into a list of page images using Pillow.
    Groups paragraphs into page-sized chunks and renders them as white-background
    JPG images so the same caption/embed pipeline works for both PDF and DOCX.
    """
    from docx import Document
    import io

    doc = Document(io.BytesIO(docx_bytes))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]

    # Group paragraphs into chunks of ~40 lines per "page"
    LINES_PER_PAGE = 40
    pages_text = []
    chunk = []
    for para in paragraphs:
        wrapped = textwrap.wrap(para, width=90)
        chunk.extend(wrapped)
        chunk.append("")  # blank line between paragraphs
        if len(chunk) >= LINES_PER_PAGE:
            pages_text.append("\n".join(chunk))
            chunk = []
    if chunk:
        pages_text.append("\n".join(chunk))

    page_folder = _get_page_folder(doc_id)
    image_paths = []

    W, H = 1240, 1754  # A4 at 150 DPI
    MARGIN = 60
    LINE_H = 28
    FONT_SIZE = 20

    for i, text in enumerate(pages_text):
        img = Image.new("RGB", (W, H), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", FONT_SIZE)
        except IOError:
            font = ImageFont.load_default()

        y = MARGIN
        for line in text.split("\n"):
            if y + LINE_H > H - MARGIN:
                break
            draw.text((MARGIN, y), line, fill=(0, 0, 0), font=font)
            y += LINE_H

        path = page_folder / f"page_{i + 1}.jpg"
        img.save(str(path), quality=90)
        image_paths.append(path)

    return image_paths


# ---------------------------------------------------------------------------
# API calls with retry (exponential backoff: 2→4→8→16→32s, max 5 attempts)
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=32),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _embed_text(text: str) -> list[float]:
    """Embed a string using Gemini text-embedding model with retry."""
    server_key = os.environ.get("GOOGLE_API_KEY")
    if not server_key:
        raise EnvironmentError("GOOGLE_API_KEY is missing from .env (required for ingestion).")
    genai.configure(api_key=server_key)

    result = genai.embed_content(
        model=EMBED_MODEL,
        content=text,
        task_type="retrieval_document",
    )
    return result["embedding"]


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=32),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _embed_query(text: str) -> list[float]:
    """Embed a query string (uses retrieval_query task type for better recall) with retry."""
    server_key = os.environ.get("GOOGLE_API_KEY")
    if not server_key:
        raise EnvironmentError("GOOGLE_API_KEY is missing from .env (required for RAG queries).")
    genai.configure(api_key=server_key)

    result = genai.embed_content(
        model=EMBED_MODEL,
        content=text,
        task_type="retrieval_query",
    )
    return result["embedding"]


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=32),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _generate_page_caption(image_path: Path) -> str:
    """
    Call Gemini Flash Vision to produce a precise, verbatim caption
    of a single PDF page image. Retries on transient API errors.
    """
    server_key = os.environ.get("GOOGLE_API_KEY")
    if not server_key:
        raise EnvironmentError("GOOGLE_API_KEY is missing from .env (required for ingestion).")
    genai.configure(api_key=server_key)

    model = genai.GenerativeModel(VISION_MODEL)
    img = Image.open(image_path)
    response = model.generate_content([
        (
            "Describe everything on this document page with extreme precision. "
            "Include ALL text, headings, table contents (every row and column), "
            "numbers, labels, and structural layout verbatim. "
            "Do not summarize or omit any detail."
        ),
        img,
    ])
    return response.text.strip()


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_document(
    file_bytes: bytes,
    filename: str,
    bot_id: int,
    doc_id: int,
) -> None:
    """
    Ingest a PDF or DOCX into the Vision RAG pipeline.
    Uses the server's GOOGLE_API_KEY for embedding generation.
    Stores relative image paths for environment portability.
    """
    page_folder = _get_page_folder(doc_id)
    collection = get_chroma_collection()
    ext = Path(filename).suffix.lower()

    try:
        # --- Render pages to images ---
        image_paths: list[Path] = []

        if ext == ".pdf":
            pdf = fitz.open(stream=file_bytes, filetype="pdf")
            for page_num in range(len(pdf)):
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom ≈ 144 DPI
                pix = pdf[page_num].get_pixmap(matrix=mat)
                img_path = page_folder / f"page_{page_num + 1}.jpg"
                pix.save(str(img_path))
                image_paths.append(img_path)
            pdf.close()

        elif ext == ".docx":
            image_paths = _render_docx_pages(file_bytes, doc_id)

        else:
            raise ValueError(f"Unsupported file type: {ext}. Only .pdf and .docx are supported.")

        page_count = len(image_paths)
        if page_count == 0:
            raise ValueError("Document produced no pages after processing.")

        # --- Caption + embed + store each page ---
        for i, image_path in enumerate(image_paths):
            caption = _generate_page_caption(image_path)
            embedding = _embed_text(caption)

            node_id = f"bot{bot_id}_doc{doc_id}_page{i + 1}"
            collection.upsert(
                ids=[node_id],
                embeddings=[embedding],
                documents=[caption],
                metadatas=[{
                    "bot_id":            str(bot_id),
                    "doc_id":            str(doc_id),
                    "page_num":          i + 1,
                    "chunk_index":       0,
                    "source_filename":   filename,
                    "source_image_path": _to_relative_path(image_path),
                }],
            )

        update_document_status(doc_id, "processed", page_count=page_count)

    except Exception as exc:
        update_document_status(doc_id, "failed")
        print(f"\n{'='*60}\nINGESTION ERROR for '{filename}':\n{'='*60}")
        print(traceback.format_exc())
        raise RuntimeError(f"Ingestion failed for '{filename}': {exc}") from exc


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_document(bot_id: int, doc_id: int) -> None:
    """
    Remove a single document: delete ChromaDB nodes + page image folder.
    Caller must separately delete the SQLite row via database.delete_document().
    """
    collection = get_chroma_collection()
    collection.delete(
        where={
            "$and": [
                {"bot_id": {"$eq": str(bot_id)}},
                {"doc_id": {"$eq": str(doc_id)}},
            ]
        }
    )
    page_folder = ASSETS_DIR / f"doc_{doc_id}"
    if page_folder.exists():
        shutil.rmtree(page_folder)


def delete_all_bot_data(bot_id: int, doc_ids: list[int]) -> None:
    """
    Remove ALL data for a bot before deleting it from SQLite.
    Deletes ChromaDB nodes for each doc and their image folders.
    Call database.delete_bot() after this to remove the bot row.
    """
    for doc_id in doc_ids:
        delete_document(bot_id, doc_id)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query_bot(
    bot_id: int,
    bot_custom_prompt: str,
    user_query: str,
    chat_history: list[dict],
    api_key: str,
    provider: str,
    model: str | None = None,
) -> str:
    """
    Answer a user question using Vision RAG:
    1. Embed the query using server GOOGLE_API_KEY to retrieve top-3 pages
    2. Load the page images from disk (resolving relative paths)
    3. Build a combined prompt
    4. Call the selected gateway provider (using user's BYOK api_key)
    """
    collection = get_chroma_collection()

    # 1. Embed query and retrieve top-K relevant page nodes (uses GOOGLE_API_KEY)
    query_embedding = _embed_query(user_query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=TOP_K,
        where={"bot_id": {"$eq": str(bot_id)}},
        include=["metadatas", "documents"],
    )

    if not results["ids"][0]:
        # No documents uploaded for this bot
        return (
            "I don't have any documents to reference yet. "
            "Please upload a PDF in the **Manage Settings** page first, "
            "or ask me a general question!"
        )

    # 2. Load the page images from metadata (resolve relative paths)
    images = []
    page_citations = []
    for meta in results["metadatas"][0]:
        rel_path = meta.get("source_image_path", "")
        if rel_path:
            abs_path = _to_absolute_path(rel_path)
            if abs_path.exists():
                images.append(Image.open(abs_path))
                page_citations.append(
                    f"Page {meta.get('page_num', '?')} of {meta.get('source_filename', 'document')}"
                )

    if not images:
        return (
            "I found relevant document entries but the page images are missing. "
            "Please re-upload the document to rebuild the knowledge base."
        )

    # 3. Build the recent chat history block (last 3 turns for context)
    history_block = ""
    if chat_history:
        recent = chat_history[-6:]  # last 3 user+assistant pairs
        for msg in recent:
            role = "User" if msg["role"] == "user" else "Assistant"
            history_block += f"{role}: {msg['content']}\n"

    # 4. Assemble the full prompt
    final_prompt = (
        f"{SYSTEM_GUARDRAILS}\n\n"
        f"Bot-Specific Instructions:\n{bot_custom_prompt}\n\n"
    )
    if history_block:
        final_prompt += f"Recent Conversation:\n{history_block}\n"
    final_prompt += f"User Question:\n{user_query}"

    # 5. Call user's chosen provider via Gateway
    from llm_gateway import LLMGateway
    gateway = LLMGateway()
    answer = gateway.generate(
        provider=provider,
        api_key=api_key,
        prompt=final_prompt,
        images=images,
        model=model,
    )

    # Append citation footnote so users know which pages were used
    if page_citations:
        citations_str = " | ".join(page_citations)
        answer += f"\n\n---\n*Sources checked: {citations_str}*"

    return answer

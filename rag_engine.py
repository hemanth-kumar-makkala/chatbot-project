"""
rag_engine.py — LlamaParse ingestion + LlamaIndex RAG with:
  - MarkdownNodeParser (header/table-boundary chunking, no arbitrary splits)
  - ContextChatEngine (conversational memory for follow-up questions)
  - ChromaDB metadata filter on bot_id (strict RAG isolation)
"""
import os
import tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── LlamaIndex core ──────────────────────────────────────────────────────────
from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    Settings as LlamaSettings,
)
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import (
    MetadataFilter,
    MetadataFilters,
    FilterOperator,
)
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.prompts import PromptTemplate

# ── Integrations ─────────────────────────────────────────────────────────────
from llama_index.llms.gemini import Gemini
from llama_index.embeddings.gemini import GeminiEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_parse import LlamaParse

# ── Local ─────────────────────────────────────────────────────────────────────
from vector_store import get_chroma_collection
from database import update_document_status

# ---------------------------------------------------------------------------
# Global LlamaIndex settings (set once, inherited everywhere)
# ---------------------------------------------------------------------------

def _configure_llama_settings() -> None:
    """Wire Gemini LLM + Embeddings into LlamaIndex's global Settings."""
    LlamaSettings.llm = Gemini(
        model="models/gemini-flash-latest",
        api_key=os.environ["GOOGLE_API_KEY"],
        temperature=0.0,
    )
    LlamaSettings.embed_model = GeminiEmbedding(
        model_name="models/gemini-embedding-001",
        api_key=os.environ["GOOGLE_API_KEY"],
    )


_configure_llama_settings()


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_document(pdf_bytes: bytes, filename: str, bot_id: int, doc_id: int) -> None:
    """
    Parse a PDF with LlamaParse, chunk using MarkdownNodeParser (so headers
    and table boundaries are respected), inject bot_id metadata into every
    node, and upsert into ChromaDB.

    Args:
        pdf_bytes:  Raw bytes of the uploaded PDF file.
        filename:   Original filename (used for metadata / display).
        bot_id:     Bot that owns this document — injected into every chunk.
        doc_id:     SQLite document row id; status is updated to 'processed'
                    on success or 'error' on failure.
    """
    try:
        # 1. Write bytes to a temp file so LlamaParse can read it
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        # 2. Parse the PDF → list of LlamaIndex Document objects (markdown text)
        parser = LlamaParse(
            api_key=os.environ["LLAMA_CLOUD_API_KEY"],
            result_type="markdown",
            verbose=False,
        )
        raw_docs = parser.load_data(tmp_path)
        os.unlink(tmp_path)  # clean up temp file

        if not raw_docs:
            raise ValueError("LlamaParse returned no content for this file.")

        # 3. Chunk using MarkdownNodeParser — splits on # headers and table edges,
        #    never mid-table or mid-section like a plain character-count splitter.
        md_parser = MarkdownNodeParser()
        nodes = md_parser.get_nodes_from_documents(raw_docs)

        if not nodes:
            raise ValueError("No nodes produced after markdown parsing.")

        # 4. Inject bot_id into every node's metadata so we can filter later.
        #    We stringify bot_id because ChromaDB metadata values must be str/int/float.
        for node in nodes:
            node.metadata["bot_id"] = str(bot_id)
            node.metadata["source_filename"] = filename
            # Exclude bot_id from the text fed to the LLM (it's housekeeping, not content)
            node.excluded_llm_metadata_keys = ["bot_id"]
            node.excluded_embed_metadata_keys = ["bot_id"]

        # 5. Build a VectorStoreIndex backed by ChromaDB and upsert the nodes
        chroma_collection = get_chroma_collection()
        vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
        storage_ctx = StorageContext.from_defaults(vector_store=vector_store)

        VectorStoreIndex(
            nodes=nodes,
            storage_context=storage_ctx,
            show_progress=False,
        )

        update_document_status(doc_id, "processed")

    except Exception as exc:  # noqa: BLE001
        update_document_status(doc_id, "error")
        raise RuntimeError(f"Ingestion failed for '{filename}': {exc}") from exc


# ---------------------------------------------------------------------------
# Chat engine factory
# ---------------------------------------------------------------------------

def get_chat_engine(
    bot_id: int,
    system_prompt: str,
    chat_history: list[dict],
) -> ContextChatEngine:
    """
    Build a ContextChatEngine for a specific bot that:
      - Retrieves only chunks where metadata.bot_id == str(bot_id)
      - Prepends the bot's custom system_prompt to every LLM call
      - Remembers the conversation via ChatMemoryBuffer

    Args:
        bot_id:        Which bot's chunks to retrieve.
        system_prompt: The bot's personality/instructions stored in SQLite.
        chat_history:  List of {"role": "user"|"assistant", "content": "..."} dicts
                       from st.session_state, replayed into the memory buffer.

    Returns:
        A ContextChatEngine ready to call `.chat(user_message)` on.
    """
    # 1. Build the retriever with a strict bot_id filter
    chroma_collection = get_chroma_collection()
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_ctx = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        storage_context=storage_ctx,
    )

    bot_filter = MetadataFilters(
        filters=[
            MetadataFilter(
                key="bot_id",
                value=str(bot_id),
                operator=FilterOperator.EQ,
            )
        ]
    )
    retriever = index.as_retriever(
        similarity_top_k=6,
        filters=bot_filter,
    )

    # 2. Replay existing chat history into a memory buffer so follow-up
    #    questions ("What medication was prescribed for that?") resolve correctly.
    memory = ChatMemoryBuffer.from_defaults(token_limit=4096)
    for msg in chat_history:
        role = MessageRole.USER if msg["role"] == "user" else MessageRole.ASSISTANT
        memory.put(ChatMessage(role=role, content=msg["content"]))

    # 3. Build a system prompt that merges the bot's persona with RAG instructions.
    #    {context_str} is injected automatically by ContextChatEngine.
    combined_system_prompt = (
        f"{system_prompt}\n\n"
        "You may be given retrieved context from the user's documents below. "
        "If context is provided, use it to answer accurately. "
        "If no context is provided or it is empty, answer from your general knowledge and let the user know no documents have been uploaded yet.\n\n"
        "Context:\n{context_str}"
    )

    # 4. Assemble the ContextChatEngine
    chat_engine = ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=memory,
        system_prompt=combined_system_prompt,
        llm=LlamaSettings.llm,
        verbose=False,
    )

    return chat_engine

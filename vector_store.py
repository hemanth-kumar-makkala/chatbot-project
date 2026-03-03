"""
vector_store.py — ChromaDB persistent client and collection accessor.
All ChromaDB setup lives here so other modules get a consistent handle.
"""
from __future__ import annotations
import chromadb
from chromadb.config import Settings
from pathlib import Path

# Persistent storage path (relative to project root)
CHROMA_DATA_PATH = str(Path(__file__).parent / "chroma_data")
COLLECTION_NAME = "chatbot_docs"

# Module-level singleton so we open the client only once per process
_client: chromadb.PersistentClient | None = None


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=CHROMA_DATA_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def get_chroma_collection() -> chromadb.Collection:
    """
    Return (or create) the single shared ChromaDB collection.
    LlamaIndex's ChromaVectorStore wraps this collection object.
    """
    client = _get_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

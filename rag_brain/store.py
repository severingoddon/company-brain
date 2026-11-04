"""Chroma collections and embedding models, cached per process."""

from __future__ import annotations

import sys
import threading
from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.config import Settings

from .config import Source

_embed_lock = threading.Lock()


@lru_cache(maxsize=4)
def embedder(model_name: str):
    # Imported here because torch takes a few seconds to load.
    import torch
    from sentence_transformers import SentenceTransformer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    # Log to stderr: stdout is the MCP stdio channel.
    print(f"[rag_brain] loading {model_name} on {device} ...", file=sys.stderr)
    model = SentenceTransformer(model_name, device=device)
    if device == "mps":
        model = model.half()  # ~15-20 % faster on Apple Silicon, same retrieval quality
    return model


def embed(source: Source, texts: list[str]) -> list[list[float]]:
    with _embed_lock:
        vecs = embedder(source.embed_model).encode(
            texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False
        )
    return vecs.tolist()


@lru_cache(maxsize=4)
def _client(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))


def collection(source: Source):
    # The embedding model is stored with the collection so a mismatch can be detected.
    col = _client(source.chroma_path).get_or_create_collection(
        source.collection,
        metadata={"hnsw:space": "cosine", "embed_model": source.embed_model},
    )
    stored = (col.metadata or {}).get("embed_model")
    if stored and stored != source.embed_model:
        raise RuntimeError(
            f"Collection '{source.collection}' was built with {stored}, but the source uses "
            f"{source.embed_model}. Rebuild it: python -m rag_brain.indexer --source {source.name} --rebuild"
        )
    return col


def drop_collection(source: Source) -> None:
    client = _client(source.chroma_path)
    if source.collection in [c.name for c in client.list_collections()]:
        client.delete_collection(source.collection)

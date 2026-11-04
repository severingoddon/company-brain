"""Semantic search over one source's index.

    python -m rag_brain.search --source company "Wie lautet die Spesenregelung?"
"""

from __future__ import annotations

import argparse

from .config import Source, get_source
from .links import link_for
from .store import collection, embed


def search(source: Source, query: str, top_k: int = 6, folder: str | None = None) -> list[dict]:
    col = collection(source)
    count = col.count()
    if count == 0:
        return []
    res = col.query(
        query_embeddings=embed(source, [query]),
        n_results=min(top_k, count),
        where={"folder": folder} if folder else None,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {
            "score": round(1 - dist, 3),  # cosine similarity
            "path": meta["path"],
            "title": meta["title"],
            "heading": meta.get("heading", ""),
            "tags": meta.get("tags", ""),
            "modified": meta.get("modified", ""),
            "text": doc,
        }
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def format_hits(hits: list[dict], source: Source) -> str:
    """Render hits as compact text for the LLM, each with a clickable link."""
    if not hits:
        return "No matches."
    out = []
    for i, h in enumerate(hits, 1):
        loc = h["path"] + (f" > {h['heading']}" if h["heading"] else "")
        meta = f"score {h['score']}, modified {h['modified']}"
        if h["tags"]:
            meta += f", tags: {h['tags']}"
        link = link_for(source, h["path"])
        out.append(f"[{i}] {loc}\nlink: {link}\n({meta})\n{h['text'].strip()}")
    return "\n\n---\n\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query")
    ap.add_argument("--source", required=True)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--folder")
    a = ap.parse_args()
    source = get_source(a.source)
    print(format_hits(search(source, a.query, a.k, a.folder), source))


if __name__ == "__main__":
    main()

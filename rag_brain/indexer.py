"""Files -> Chroma. Incremental: only new or changed files are re-embedded.

    python -m rag_brain.indexer --source company          # incremental
    python -m rag_brain.indexer --source company --rebuild # rebuild everything
    python -m rag_brain.indexer --all                     # every source in sources.toml

Files are converted in parallel threads; chunks from many files are embedded in large batches.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

from .chunking import chunk_document
from .config import Source, get_source, load_sources
from .loaders import Document, load
from .store import collection, drop_collection, embed

LOAD_WORKERS = 8
EMBED_BATCH = 256  # chunks per embed + Chroma write


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def iter_files(source: Source):
    """Yield (absolute path, source-relative POSIX path) for every file matched by include/exclude."""
    seen = set()
    for pattern in source.include:
        for p in sorted(source.path.glob(pattern)):
            if not p.is_file() or p in seen:
                continue
            rel = p.relative_to(source.path).as_posix()
            if source.skip_hidden and any(part.startswith(".") for part in rel.split("/")):
                continue
            if any(fnmatch(rel, ex) for ex in source.exclude):
                continue
            seen.add(p)
            yield p, rel


def _indexed_hashes(col) -> dict[str, str]:
    got = col.get(include=["metadatas"])
    return {m["path"]: m["hash"] for m in got["metadatas"] if m}


def index_source(source: Source, rebuild: bool = False, log=_log) -> dict:
    if not source.path.is_dir():
        raise FileNotFoundError(f"Source path not found: {source.path}")

    t0 = time.time()
    if rebuild:
        drop_collection(source)
    col = collection(source)
    known = _indexed_hashes(col)

    # 1. Find new/changed files by content hash
    files = list(iter_files(source))
    seen = {rel for _, rel in files}
    todo = []
    for path, rel in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if known.get(rel) != digest:
            todo.append((path, rel, digest))
    log(f"[{source.name}] {len(files)} files, {len(todo)} new or changed")

    def safe_load(item) -> Document | None | Exception:
        try:
            return load(item[0], source)
        except Exception as e:  # one broken file must not stop the run
            return e

    # 2. Convert in parallel, embed + write in large batches
    buf_ids, buf_docs, buf_texts, buf_meta = [], [], [], []

    def flush():
        if buf_ids:
            col.add(ids=buf_ids, documents=buf_docs, embeddings=embed(source, buf_texts), metadatas=buf_meta)
            for b in (buf_ids, buf_docs, buf_texts, buf_meta):
                b.clear()

    added = updated = skipped = new_chunks = 0
    with ThreadPoolExecutor(LOAD_WORKERS) as pool:
        for n, ((path, rel, digest), doc) in enumerate(zip(todo, pool.map(safe_load, todo)), 1):
            if isinstance(doc, Exception) or doc is None:
                skipped += 1
                log(f"  ! {rel}: {doc if doc else 'no text / unsupported type'}")
                continue
            if rel in known:
                col.delete(where={"path": rel})
                updated += 1
            else:
                added += 1

            chunks = chunk_document(doc, rel, source.chunk_size, source.chunk_overlap)
            modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            folder = rel.split("/")[0] if "/" in rel else ""
            for c in chunks:
                buf_ids.append(f"{rel}::{c.index}")
                buf_docs.append(c.body)
                buf_texts.append(c.text)
                buf_meta.append({
                    "path": rel,
                    "title": doc.title,
                    "folder": folder,
                    "type": path.suffix.lower().lstrip("."),
                    "heading": c.heading,
                    "chunk": c.index,
                    "tags": ",".join(doc.tags),
                    "links": ",".join(doc.links)[:2000],
                    "modified": modified,
                    "hash": digest,
                })
            new_chunks += len(chunks)
            log(f"  + [{n}/{len(todo)}] {rel} ({len(chunks)} chunks)")
            if len(buf_ids) >= EMBED_BATCH:
                flush()
    flush()

    # 3. Drop files that no longer exist
    removed = 0
    for rel in set(known) - seen:
        col.delete(where={"path": rel})
        removed += 1
        log(f"  - {rel}")

    stats = {
        "source": source.name,
        "added": added,
        "updated": updated,
        "removed": removed,
        "skipped": skipped,
        "new_chunks": new_chunks,
        "total_chunks": col.count(),
        "seconds": round(time.time() - t0, 1),
    }
    log(f"Done: {stats}")
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", help="name of a [sources.<name>] block in sources.toml")
    group.add_argument("--all", action="store_true", help="index every source")
    ap.add_argument("--rebuild", action="store_true", help="drop and rebuild the index")
    args = ap.parse_args()

    sources = list(load_sources().values()) if args.all else [get_source(args.source)]
    for source in sources:
        _log(f"[{source.name}] {source.path}")
        index_source(source, rebuild=args.rebuild)


if __name__ == "__main__":
    main()

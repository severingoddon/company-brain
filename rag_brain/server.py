"""MCP server for one data source (read-only). Register one server per source in OpenClaw.

stdio (default, OpenClaw spawns the process itself):
    python -m rag_brain.server --source company
Streamable HTTP (for debugging, e.g. with the MCP Inspector):
    python -m rag_brain.server --source company --http --port 8765
"""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .config import Source, get_source
from .indexer import index_source, iter_files
from .links import link_for, start_link_server
from .loaders import load
from .search import format_hits, search
from .store import embedder


# Safety hints let OpenClaw run these tools without an approval prompt.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# reindex only writes to our own Chroma index, never to the source files.
INDEX_UPDATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)  # stdout is reserved for the MCP protocol


def build_server(source: Source) -> MCPServer:
    index_lock = threading.Lock()
    about = source.description or f"Documents in {source.path}"

    mcp = MCPServer(
        source.name,
        instructions=(
            f"{about}\nUse `search_docs` first, then `read_document` for the full file if needed. "
            "Cite sources as Markdown links: [path](link), using the link given with each result."
        ),
    )

    def resolve(path: str) -> Path:
        """Map a source-relative path to a file, refusing anything outside the source."""
        p = (source.path / path.lstrip("/")).resolve()
        if not p.is_relative_to(source.path):
            raise ToolError("Path is outside the data source.")
        if not p.is_file() and p.with_name(p.name + ".md").is_file():
            p = p.with_name(p.name + ".md")  # allow note names without extension
        if not p.is_file():
            raise ToolError(f"Not found: {path}. Use list_documents or search_docs to find valid paths.")
        return p

    def search_docs(query: str, top_k: int = 6, folder: str | None = None) -> str:
        top_k = max(1, min(int(top_k), 20))
        return format_hits(search(source, query, top_k, folder or None), source)

    # The docstring becomes the tool description, so it includes the source description.
    search_docs.__doc__ = f"""Semantic search. {about}

        Args:
            query: Question or keywords, ideally a full sentence (German or English).
            top_k: Number of results (1-20).
            folder: Optionally restrict to one top-level folder, e.g. "contracts".
        """
    mcp.tool(annotations=READ_ONLY)(search_docs)

    @mcp.tool(annotations=READ_ONLY)
    def read_document(path: str) -> str:
        """Read a whole file. `path` is the path shown in the search results."""
        p = resolve(path)
        doc = load(p, source)  # reuse the loaders so PDFs etc. come back as text
        text = doc.text if doc else p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(source.path).as_posix()
        modified = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
        return f"# {rel} (modified {modified})\nlink: {link_for(source, rel)}\n\n{text}"

    @mcp.tool(annotations=READ_ONLY)
    def list_documents(folder: str | None = None, recent: int = 0) -> str:
        """List files in the data source.

        Args:
            folder: Optionally only files below this folder.
            recent: If > 0, only the N most recently modified files, newest first.
        """
        prefix = folder.strip("/") + "/" if folder else ""
        files = [(p, rel) for p, rel in iter_files(source) if rel.startswith(prefix)]
        if recent > 0:
            files = sorted(files, key=lambda f: f[0].stat().st_mtime, reverse=True)[:recent]
        lines = [f"{rel}  ({datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d})" for p, rel in files]
        return "\n".join(lines) or "No files found."

    @mcp.tool(annotations=INDEX_UPDATE)
    def reindex() -> str:
        """Update the search index (changed files only). Call this when the user says files have changed."""
        with index_lock:
            stats = index_source(source, log=_log)
        return f"Index updated: {stats}"

    def warmup():
        """Load the model and run an incremental index so the first search is fast and current."""
        try:
            embedder(source.embed_model)
            with index_lock:
                index_source(source, log=_log)
        except Exception as e:  # keep the server alive regardless
            _log(f"[rag_brain] warmup failed: {e}")

    mcp.warmup = warmup  # type: ignore[attr-defined]
    return mcp


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="name of a [sources.<name>] block in sources.toml")
    ap.add_argument("--http", action="store_true", help="use streamable-http instead of stdio")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-warmup", action="store_true")
    a = ap.parse_args()

    source = get_source(a.source)
    mcp = build_server(source)
    start_link_server(source.link_port)
    if not a.no_warmup:
        threading.Thread(target=mcp.warmup, daemon=True).start()
    if a.http:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=a.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

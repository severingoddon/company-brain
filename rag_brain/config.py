"""Load data source definitions from sources.toml.

The config file can be overridden with RAG_BRAIN_SOURCES=/path/to/sources.toml.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = Path(os.environ.get("RAG_BRAIN_SOURCES", PROJECT_ROOT / "sources.toml"))


@dataclass(frozen=True)
class Source:
    name: str
    path: Path
    description: str = ""
    include: tuple[str, ...] = ("**/*",)  # every file a loader understands
    exclude: tuple[str, ...] = ()
    collection: str = ""
    chroma_path: Path = PROJECT_ROOT / "data" / "chroma"
    embed_model: str = "BAAI/bge-m3"
    chunk_size: int = 1500
    chunk_overlap: int = 200
    skip_hidden: bool = True
    # Local HTTP server that shows source files in the browser (see links.py)
    link_port: int = 8766
    # Optional: describe images with a vision LLM (OpenAI-compatible API) while indexing
    describe_images: bool = False
    vision_base_url: str = "http://192.168.1.28:8000/v1"
    vision_model: str = "nvidia/Qwen3.8-Flash-Next-FP4-Local"


_FIELDS = {f.name for f in fields(Source)} - {"name"}


def _abs(p: str | Path) -> Path:
    p = Path(p).expanduser()
    return (p if p.is_absolute() else PROJECT_ROOT / p).resolve()


def load_sources(file: Path = SOURCES_FILE) -> dict[str, Source]:
    with open(file, "rb") as f:
        cfg = tomllib.load(f)
    defaults = cfg.get("defaults", {})

    sources = {}
    for name, raw in cfg.get("sources", {}).items():
        merged = {**defaults, **raw}
        unknown = set(merged) - _FIELDS
        if unknown:
            raise ValueError(f"Source '{name}' in {file}: unknown keys {sorted(unknown)}")
        if "path" not in merged:
            raise ValueError(f"Source '{name}' in {file} has no 'path'.")
        merged["path"] = _abs(merged["path"])
        if "chroma_path" in merged:
            merged["chroma_path"] = _abs(merged["chroma_path"])
        for key in ("include", "exclude"):
            if key in merged:
                merged[key] = tuple(merged[key])
        merged["description"] = merged.get("description", "").strip()
        merged["collection"] = merged.get("collection") or name
        sources[name] = Source(name=name, **merged)
    return sources


def get_source(name: str) -> Source:
    sources = load_sources()
    if name not in sources:
        raise SystemExit(f"Unknown source '{name}'. Defined in {SOURCES_FILE}: {', '.join(sources) or '-'}")
    return sources[name]

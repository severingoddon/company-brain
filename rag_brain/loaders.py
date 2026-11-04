"""File loaders: turn any file into a Document (text, preferably Markdown).

Resolution order for a file:
  1. a loader registered for its suffix (Markdown, plain text, PDF, images)
  2. MarkItDown as a catch-all (docx, pptx, xlsx, xls, csv, html, json, xml, epub, msg, zip, ...)
  3. otherwise the file is skipped

Add a custom type:

    @register(".foo")
    def load_foo(path: Path, source: Source) -> Document: ...
"""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import frontmatter

from .config import Source

WIKILINK_RE = re.compile(r"!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
TAG_RE = re.compile(r"(?<![\w/#])#([A-Za-zÄÖÜäöü0-9_\-/]+)")

TEXT_SUFFIXES = {
    ".txt", ".text", ".rst", ".log", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".cs",
    ".rb", ".php", ".sh", ".zsh", ".sql", ".r", ".swift", ".scala", ".tf", ".proto", ".graphql",
    ".srt", ".vtt", ".tex", ".eml",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
# Never worth sending to MarkItDown
BINARY_SKIP = {".exe", ".dll", ".so", ".dylib", ".bin", ".dmg", ".iso", ".pyc", ".o", ".class",
               ".sqlite", ".sqlite3", ".db", ".parquet", ".pkl", ".pt", ".safetensors", ".onnx",
               ".mp4", ".mov", ".avi", ".mkv", ".woff", ".woff2", ".ttf", ".otf", ".ico"}


@dataclass
class Document:
    title: str
    text: str
    markdown: bool = False  # True -> chunker splits at Markdown headings
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


Loader = Callable[[Path, Source], Document]
LOADERS: dict[str, Loader] = {}


def register(*suffixes: str):
    def deco(fn: Loader) -> Loader:
        for s in suffixes:
            LOADERS[s.lower()] = fn
        return fn

    return deco


def load(path: Path, source: Source) -> Document | None:
    """Load a file, or return None if it cannot be turned into text."""
    suffix = path.suffix.lower()
    if suffix in BINARY_SKIP:
        return None
    loader = LOADERS.get(suffix, _load_markitdown)
    doc = loader(path, source)
    if doc is None or not doc.text.strip():
        return None
    return doc


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@register(".md", ".markdown", ".mdx")
def load_markdown(path: Path, source: Source) -> Document:
    """Markdown, including frontmatter tags, #tags and wiki-style [[links]]."""
    raw = _read(path)
    try:
        post = frontmatter.loads(raw)
        body, meta = post.content, post.metadata
    except Exception:  # broken frontmatter: index the file anyway
        body, meta = raw, {}

    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    tag_set = {str(t).lstrip("#") for t in tags} | set(TAG_RE.findall(body))
    links = sorted({m.group(1).strip() for m in WIKILINK_RE.finditer(body)})
    # [[Note|Alias]] -> Alias, [[Note]] -> Note
    body = WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), body)
    title = str(meta.get("title") or path.stem)
    return Document(title=title, text=body, markdown=True, tags=sorted(tag_set), links=links)


@register(*TEXT_SUFFIXES)
def load_text(path: Path, source: Source) -> Document:
    return Document(title=path.stem, text=_read(path))


@register(".pdf")
def load_pdf(path: Path, source: Source) -> Document:
    """PDF with page markers, so answers can cite page numbers."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = [f"[Page {i}]\n{(p.extract_text() or '').strip()}" for i, p in enumerate(reader.pages, 1)]
    title = (reader.metadata.title if reader.metadata and reader.metadata.title else None) or path.stem
    text = "\n\n".join(pages)
    if len(text.replace("[Page", "").strip()) < 50 * len(pages):  # probably scanned: try MarkItDown
        return _load_markitdown(path, source) or Document(title=str(title), text=text)
    return Document(title=str(title), text=text)


@register(*IMAGE_SUFFIXES)
def load_image(path: Path, source: Source) -> Document | None:
    """Images are only indexed when a vision model is configured (describe_images = true)."""
    if not source.describe_images:
        return None
    from openai import OpenAI

    client = OpenAI(base_url=source.vision_base_url, api_key="not-needed", timeout=120)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode()
    resp = client.chat.completions.create(
        model=source.vision_model,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image for a search index. Transcribe all visible "
                                         "text verbatim, then summarize what it shows (charts: axes, values, trends)."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
            ],
        }],
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return Document(title=path.stem, text=resp.choices[0].message.content or "")


@lru_cache(maxsize=1)
def _markitdown():
    from markitdown import MarkItDown

    return MarkItDown(enable_plugins=False)


def _load_markitdown(path: Path, source: Source) -> Document | None:
    """Catch-all converter for Office files, HTML, CSV, JSON, XML, e-mails, EPUB, ZIP, ..."""
    try:
        result = _markitdown().convert(str(path))
    except Exception:
        return None
    title = (getattr(result, "title", None) or path.stem).strip()
    return Document(title=title, text=result.text_content or "", markdown=True)

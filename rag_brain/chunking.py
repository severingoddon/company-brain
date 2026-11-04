"""Split a Document into chunks. Markdown is split at headings first, so each chunk knows its section."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .loaders import Document

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Chunk:
    text: str  # text that gets embedded (includes title/section as context)
    body: str  # plain content returned to the agent
    heading: str
    index: int


def _markdown_sections(body: str) -> list[tuple[str, str]]:
    """Split at headings -> [(heading path, text)]. Ignores '#' inside code fences."""
    sections: list[tuple[str, str]] = []
    stack: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append((" > ".join(stack), text))
        buf.clear()

    for line in body.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            stack[:] = stack[: level - 1] + [m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return sections


def _is_table(para: str) -> bool:
    lines = para.strip().splitlines()
    return len(lines) > 2 and all(l.lstrip().startswith("|") for l in lines[:3])


def _split_table(para: str, size: int) -> list[str]:
    """Split a Markdown table by rows; every piece repeats the header so rows stay readable."""
    lines = para.strip().splitlines()
    header, rows = "\n".join(lines[:2]), lines[2:]
    pieces, cur = [], []
    for row in rows:
        if cur and len(header) + sum(len(r) + 1 for r in cur) + len(row) > size:
            pieces.append(header + "\n" + "\n".join(cur))
            cur = []
        cur.append(row[: size - len(header) - 1])  # clamp absurdly wide rows
    if cur:
        pieces.append(header + "\n" + "\n".join(cur))
    return pieces


def split_text(text: str, size: int, overlap: int) -> list[str]:
    """Split text at paragraph boundaries into pieces of at most `size` characters."""
    if len(text) <= size:
        return [text]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if len(para) > size and _is_table(para):  # CSV / Excel sheets
            if cur:
                parts.append(cur)
                cur = ""
            parts.extend(_split_table(para, size))
            continue
        while len(para) > size:  # a single huge paragraph (e.g. a code block)
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(para[:size])
            para = para[size - overlap :]
        if cur and len(cur) + len(para) + 2 > size:
            parts.append(cur)
            cur = f"{cur[-overlap:]}\n\n{para}" if overlap else para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur.strip():
        parts.append(cur)
    return parts


def chunk_document(doc: Document, rel_path: str, size: int, overlap: int) -> list[Chunk]:
    sections = _markdown_sections(doc.text) if doc.markdown else [("", doc.text.strip())]
    chunks: list[Chunk] = []
    for heading, text in sections:
        if not text:
            continue
        for piece in split_text(text, size, overlap):
            ctx = f"Title: {doc.title}\nPath: {rel_path}"
            if heading:
                ctx += f"\nSection: {heading}"
            chunks.append(Chunk(text=f"{ctx}\n\n{piece}", body=piece, heading=heading, index=len(chunks)))
    if not chunks:  # empty file: index at least the title
        chunks.append(Chunk(text=f"Title: {doc.title}\nPath: {rel_path}", body="", heading="", index=0))
    return chunks

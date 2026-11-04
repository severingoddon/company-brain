"""Clickable source links: show any source file in the browser.

Every search hit carries a loopback URL

    http://127.0.0.1:<link_port>/open/<source>/<path>

served by a tiny HTTP server inside the MCP process (the OpenClaw Control UI only renders
http(s) links, so file:// paths would not be clickable):

  - PDFs, images and plain text are shown as-is by the browser
  - Markdown is rendered to HTML
  - everything else (docx, pptx, xlsx, html, ...) is converted with the loaders and rendered,
    with a link to download the original (?raw=1)

Several MCP processes may run at once; whichever binds the port first serves all sources,
the others keep retrying in the background in case that process exits.
"""

from __future__ import annotations

import html
import mimetypes
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .config import Source, load_sources

HOST = "127.0.0.1"

# Shown natively by the browser
NATIVE_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp3", ".wav", ".mp4", ".mov"}
# No scripts, no external requests on rendered pages and text files.
SAFE_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
:root {{ color-scheme: light dark; }}
body {{ font: 16px/1.6 -apple-system, system-ui, sans-serif; max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }}
header {{ font-size: 13px; opacity: .7; border-bottom: 1px solid #8884; padding-bottom: 8px; margin-bottom: 24px; word-break: break-all; }}
pre {{ background: #8881; padding: 12px; overflow-x: auto; border-radius: 6px; }}
code {{ font: 13px ui-monospace, Menlo, monospace; }}
table {{ border-collapse: collapse; display: block; overflow-x: auto; }}
th, td {{ border: 1px solid #8885; padding: 4px 8px; text-align: left; vertical-align: top; }}
img {{ max-width: 100%; }}
</style></head><body><header>{source} / {path}{note}</header>{body}</body></html>"""


def link_for(source: Source, rel_path: str) -> str:
    return f"http://{HOST}:{source.link_port}/open/{quote(source.name)}/{quote(rel_path)}"


def _render_markdown(text: str) -> str:
    import markdown

    return markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc"])


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep stdout/stderr clean
        pass

    def _send(self, code: int, body: bytes, ctype: str, csp: bool = True, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if csp:
            self.send_header("Content-Security-Policy", SAFE_CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, code: int, msg: str):
        self._send(code, msg.encode(), "text/plain; charset=utf-8")

    def do_GET(self):
        # Reject foreign Host headers (DNS rebinding protection).
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in (HOST, "localhost"):
            return self._fail(403, "Forbidden")

        url = urlsplit(self.path)
        parts = url.path.split("/", 3)  # ["", "open", source, path]
        if len(parts) != 4 or parts[1] != "open":
            return self._fail(404, "Not found")
        source = load_sources().get(unquote(parts[2]))
        if source is None:
            return self._fail(404, "Unknown source")

        rel = unquote(parts[3])
        file = (source.path / rel).resolve()
        if not file.is_relative_to(source.path) or not file.is_file():
            return self._fail(404, f"Not found: {rel}")

        raw = "raw" in parse_qs(url.query)
        suffix = file.suffix.lower()
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"

        if suffix in NATIVE_TYPES:
            return self._send(200, file.read_bytes(), ctype, csp=False)
        if raw:
            return self._send(200, file.read_bytes(), ctype,
                              extra={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.name)}"})
        self._send(200, self._render(source, file, rel).encode(), "text/html; charset=utf-8")

    def _render(self, source: Source, file: Path, rel: str) -> str:
        from .loaders import TEXT_SUFFIXES, load

        suffix = file.suffix.lower()
        note = ""
        if suffix in TEXT_SUFFIXES:
            body = f"<pre><code>{html.escape(file.read_text(encoding='utf-8', errors='replace'))}</code></pre>"
        else:
            doc = load(file, source)
            if doc is None:
                body = "<p>No preview available for this file type.</p>"
            elif doc.markdown:
                body = _render_markdown(doc.text)
            else:
                body = f"<pre>{html.escape(doc.text)}</pre>"
            if suffix not in {".md", ".markdown", ".mdx"}:
                note = f' · converted preview · <a href="?raw=1">download original</a>'
        return PAGE.format(title=html.escape(file.name), source=html.escape(source.name),
                           path=html.escape(rel), note=note, body=body)


def start_link_server(port: int) -> None:
    """Serve links in a daemon thread. Retries every 30 s while another process holds the port."""

    def run():
        while True:
            try:
                server = ThreadingHTTPServer((HOST, port), _Handler)
            except OSError:
                time.sleep(30)
                continue
            print(f"[rag_brain] link server on http://{HOST}:{port}", file=sys.stderr)
            server.serve_forever()

    threading.Thread(target=run, daemon=True, name="link-server").start()

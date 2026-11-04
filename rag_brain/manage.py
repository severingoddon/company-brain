"""Manage data sources. Exactly one source is active in OpenClaw at a time.

    python -m rag_brain.manage add <name> <folder> "<description>" [--include GLOB] [--exclude GLOB]
                                   [--describe-images] [--no-activate]
    python -m rag_brain.manage use <name>      # switch the active source
    python -m rag_brain.manage list            # all sources, * marks the active one
    python -m rag_brain.manage remove <name>   # drop index + OpenClaw entry (files are untouched)

`add` writes the sources.toml block, builds the index, registers an MCP server in OpenClaw and
makes it the active source. Inactive sources keep their index, so switching back is instant.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import PROJECT_ROOT, SOURCES_FILE, get_source, load_sources
from .indexer import index_source
from .store import collection, drop_collection

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


# ---------------------------------------------------------------- sources.toml

def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(_toml_str(i) for i in items) + "]"


def append_source(name: str, folder: Path, description: str, include: list[str], exclude: list[str],
                  describe_images: bool) -> None:
    block = [
        "",
        f"[sources.{name}]",
        f"description = {_toml_str(description)}",
        f"path = {_toml_str(str(folder))}",
        f"include = {_toml_list(include)}",
    ]
    if exclude:
        block.append(f"exclude = {_toml_list(exclude)}")
    if describe_images:
        block.append("describe_images = true")
    with open(SOURCES_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")


def remove_source_block(name: str) -> None:
    text = SOURCES_FILE.read_text(encoding="utf-8")
    pattern = re.compile(rf"\n*\[sources\.{re.escape(name)}\]\n.*?(?=\n\[|\n# Example|\Z)", re.S)
    SOURCES_FILE.write_text(pattern.sub("\n", text).rstrip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------- OpenClaw

def _openclaw(*args: str, stdin: str | None = None, capture: bool = False) -> str:
    if not shutil.which("openclaw"):
        sys.exit("openclaw not found on PATH.")
    res = subprocess.run(["openclaw", *args], input=stdin, text=True, check=True,
                         capture_output=capture)
    return res.stdout if capture else ""


def _patch(servers: dict) -> None:
    _openclaw("config", "patch", "--stdin", stdin=json.dumps({"mcp": {"servers": servers}}))


def our_servers() -> dict[str, bool]:
    """MCP servers in OpenClaw that run rag_brain.server -> enabled flag."""
    try:
        servers = json.loads(_openclaw("config", "get", "mcp.servers", "--json", capture=True) or "{}")
    except subprocess.CalledProcessError:
        return {}
    return {
        name: cfg.get("enabled", True)
        for name, cfg in (servers or {}).items()
        if "rag_brain.server" in (cfg.get("args") or [])
    }


def _server_config(name: str) -> dict:
    return {
        "enabled": True,
        "transport": "stdio",
        "command": str(PYTHON),
        "args": ["-m", "rag_brain.server", "--source", name],
        "cwd": str(PROJECT_ROOT),
        "env": {"TOKENIZERS_PARALLELISM": "false", "HF_HUB_DISABLE_TELEMETRY": "1"},
        "connectionTimeoutMs": 60000,
        "requestTimeoutMs": 120000,
    }


def activate(name: str) -> None:
    """Enable `name` (registering it if needed) and disable every other rag_brain server."""
    get_source(name)  # fail early on typos
    servers = our_servers()
    patch = {other: {"enabled": False} for other, on in servers.items() if other != name and on}
    patch[name] = _server_config(name) if name not in servers else {"enabled": True}
    _patch(patch)
    _openclaw("mcp", "reload")
    print(f"Active source: {name}  (tools: {name}__search_docs, {name}__read_document, ...)")
    print("Start a new chat session in the browser so the agent only sees this source.")


# ---------------------------------------------------------------- commands

def cmd_add(a) -> None:
    if not NAME_RE.match(a.name):
        sys.exit("Name must be lowercase letters, digits or '_', starting with a letter.")
    if a.name in load_sources():
        sys.exit(f"Source '{a.name}' already exists. Switch to it with: use {a.name}")
    folder = Path(a.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")

    description = a.description or f"Documents from {folder.name}."
    append_source(a.name, folder, description, a.include or ["**/*"], a.exclude, a.describe_images)
    print(f"Added [sources.{a.name}] to {SOURCES_FILE}")

    t0 = time.time()
    index_source(get_source(a.name))
    print(f"Indexed in {time.time() - t0:.0f} s")

    if a.no_activate:
        _patch({a.name: {**_server_config(a.name), "enabled": False}})
        print(f"Registered '{a.name}' in OpenClaw (inactive). Activate with: use {a.name}")
    else:
        activate(a.name)


def cmd_use(a) -> None:
    activate(a.name)


def cmd_list(a) -> None:
    servers = our_servers()
    for name, src in load_sources().items():
        try:
            chunks = collection(src).count()
        except Exception as e:
            chunks = f"error: {e}"
        mark = "*" if servers.get(name) else " "
        state = "active" if servers.get(name) else ("inactive" if name in servers else "not registered")
        print(f"{mark} {name:<16} {state:<15} {chunks} chunks  {src.path}")


def cmd_remove(a) -> None:
    source = get_source(a.name)
    drop_collection(source)
    remove_source_block(a.name)
    if a.name in our_servers():
        _patch({a.name: None})
        _openclaw("mcp", "reload")
    print(f"Removed source '{a.name}' (files in {source.path} are untouched).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="add, index and activate a new source")
    p.add_argument("name", help="short id, e.g. 'company'")
    p.add_argument("folder", help="folder with the files")
    p.add_argument("description", nargs="?", default="", help="what the files are about (shown to the agent)")
    p.add_argument("--include", action="append", help='glob, repeatable (default: "**/*")')
    p.add_argument("--exclude", action="append", default=[], help="glob to skip, repeatable")
    p.add_argument("--describe-images", action="store_true", help="caption images with the vision LLM")
    p.add_argument("--no-activate", action="store_true", help="register but keep the current source active")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("use", help="switch the active source")
    p.add_argument("name")
    p.set_defaults(func=cmd_use)

    p = sub.add_parser("list", help="list sources")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="remove a source (index + OpenClaw entry)")
    p.add_argument("name")
    p.set_defaults(func=cmd_remove)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

# RAG_BRAIN

A local knowledge assistant on top of any document collection (e.g. company data: documentation,
meeting notes, FAQs, policies, tickets). **OpenClaw** (browser UI, agent) on the Mac uses a local
**ChromaDB** index through an MCP server. The LLM runs on the **DGX Spark** (vLLM via sparkrun).

```
Mac                                                       DGX Spark 192.168.1.28
┌──────────────────────────────────────────────┐          ┌─────────────────────────────┐
│ Browser ── OpenClaw Control UI :18789         │          │ vLLM (sparkrun)        :8000│
│              │ OpenClaw gateway (loopback)    │ ──LAN──▶ │ Qwen3.8 Flash Next FP4      │
│              │ MCP stdio                      │          │ (inference + image captions)│
│              ▼                                │          └─────────────────────────────┘
│ rag_brain.server --source <name>              │
│   ├── bge-m3 embeddings (MPS)                 │
│   └── ChromaDB  data/chroma                   │
│          ▲ indexer                            │
│ data folder (PDF, Office, Markdown, CSV, ...) │
└──────────────────────────────────────────────┘
```

## Layout

| Path | Purpose |
|---|---|
| `sources.toml` | Data sources. One `[sources.<name>]` block = one index + one MCP server. |
| `rag_brain/config.py` | Loads `sources.toml`. |
| `rag_brain/loaders.py` | File -> text: Markdown (frontmatter, #tags, `[[links]]`), plain text/code, PDF (with page markers), images (optional vision captions), and MarkItDown for everything else (docx, pptx, xlsx, csv, html, json, xml, epub, msg, zip, ...). |
| `rag_brain/manage.py` | `add` / `use` / `list` / `remove` sources. Exactly one source is active in OpenClaw. |
| `rag_brain/chunking.py` | Heading-aware chunking; large tables are split by rows with the header repeated. |
| `rag_brain/indexer.py` | Incremental indexing (SHA-256 per file), parallel conversion, batched embedding. |
| `rag_brain/search.py` | Semantic search + CLI. |
| `rag_brain/server.py` | MCP server: `search_docs`, `read_document`, `list_documents`, `reindex`. |
| `rag_brain/links.py` | Clickable sources: `http://127.0.0.1:8766/open/<source>/<path>` shows the file in the browser (PDF/images natively, everything else rendered to HTML, `?raw=1` downloads the original). The Control UI only renders http(s) links. |
| `openclaw/openclaw.patch.json5` | Settings merged into `~/.openclaw/openclaw.json` (model, hardening). |
| `openclaw/workspace/` | Agent workspace: `AGENTS.md` (rules), `SOUL.md`, `IDENTITY.md`, `USER.md`. |
| `scripts/start.sh` | Check the Spark, start the gateway, open the browser UI. |
| `scripts/new_source.sh` | Add + index + activate a source (`manage add`). |
| `scripts/use_source.sh` | Switch the active source, list sources, remove one. |

## Quick start

```bash
# 1. Spark: model (inference only)
ssh goddoni@192.168.1.28
sparkrun status
sparkrun run @eugr/qwen3.8-flash-next-nvfp4-solo

# 2. Mac: add the data (index + register in OpenClaw + activate)
cd ~/Desktop/docs/Coding/PycharmProjects/RAG_BRAIN
scripts/new_source.sh company ~/Downloads/hackathon-data \
  "Acme Corp: documentation, meeting notes, FAQs, policies, product info, support tickets."

# 3. Mac: gateway + browser
scripts/start.sh            # or: openclaw gateway run   +   openclaw dashboard
```

`openclaw dashboard` opens a one-time pairing link (valid 10 min) to `http://127.0.0.1:18789/`.

Check retrieval quality from the command line before demoing:

```bash
.venv/bin/python -m rag_brain.search --source company "Wie lautet die Spesenregelung?"
```

## Sources: one active at a time

Only **one** source is active in OpenClaw; the others stay indexed but disabled, so switching
back is instant.

```bash
scripts/new_source.sh company <folder> "<description>"                     # add + index + activate
scripts/new_source.sh company <folder> "<description>" --describe-images   # caption images via the Spark
scripts/new_source.sh company <folder> "<description>" --exclude "archive/**"
scripts/new_source.sh other <folder> "<description>" --no-activate         # index, keep current source

scripts/use_source.sh                 # list (* = active)
scripts/use_source.sh company         # switch to 'company'
scripts/use_source.sh --remove company   # delete index + OpenClaw entry (files untouched)
```

After switching, start a **new chat session** in the browser so the old conversation does not
mix sources.

- The **description matters**: it becomes part of the tool description, and the agent picks
  sources by it.
- Tools appear as `company__search_docs`, `company__read_document`, ... `AGENTS.md` is
  source-agnostic, so nothing else needs editing.
- Unsupported or empty files are listed with `!` and skipped.
- New or changed files in the folder: `.venv/bin/python -m rag_brain.indexer --source company`
  (incremental), or tell the agent that files changed and it calls `company__reindex`.
- Speed on the M-series Mac: ~25 chunks/s end to end (embedding itself ~85 chunks/s).
  Image captions take ~3 s per image on the Spark.

Each source gets its own Chroma collection, so indexes never mix. Changing `embed_model`
for an existing source requires `--rebuild` (the indexer refuses to mix models).

New file types: add a function decorated with `@register(".ext")` in `rag_brain/loaders.py`.

## Setup from scratch

```bash
# Python (3.14 venv)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # first indexing run downloads bge-m3 (2.2 GB)

# OpenClaw (needs Node >= 24.16)
brew upgrade node@24
npm install -g openclaw@latest
openclaw onboard --non-interactive --accept-risk --mode local \
  --auth-choice vllm --custom-base-url "http://192.168.1.28:8000/v1" --custom-api-key vllm-local \
  --custom-model-id "nvidia/Qwen3.8-Flash-Next-FP4-Local" \
  --gateway-bind loopback --gateway-auth token --gateway-port 18789 \
  --workspace "$PWD/openclaw/workspace" \
  --skip-channels --skip-search --skip-skills --skip-hooks --skip-daemon
openclaw config patch --file openclaw/openclaw.patch.json5

# then add a source (see Quick start) and verify it
openclaw mcp doctor company --probe
```

Optional autostart of the gateway at login (macOS LaunchAgent): `openclaw gateway install`.

## Security

- Gateway listens on `127.0.0.1` only, token auth, no channels configured (browser only).
- Tool profile `coding` with `group:runtime` denied and `exec.security: deny` (no shell),
  file tools limited to the workspace, elevated mode off.
- The MCP server is read-only on the data source; `read_document` refuses paths outside it.
- `data/` (indexes, local data copies) is git-ignored.
- vLLM on the Spark has no auth and is open on the LAN. Not usable on the company VPN.

## Troubleshooting

| Problem | Check |
|---|---|
| Agent doesn't answer | `curl http://192.168.1.28:8000/v1/models`, VPN off? |
| Agent doesn't use the data | `openclaw mcp probe <source>`; `scripts/use_source.sh` shows the active one; Control UI → Settings → MCP |
| Search results outdated | `.venv/bin/python -m rag_brain.indexer --source <source>` |
| Tool calls show up as plain text | vLLM needs `--enable-auto-tool-choice --tool-call-parser qwen3_xml` |
| `embed_model` mismatch error | `python -m rag_brain.indexer --source <name> --rebuild` |

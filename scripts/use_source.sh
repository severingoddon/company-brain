#!/usr/bin/env bash
# Switch the active source, or list sources when called without arguments.
#   scripts/use_source.sh              # list (* = active)
#   scripts/use_source.sh company      # make 'company' the only active source
#   scripts/use_source.sh --remove x   # delete source 'x' (index + OpenClaw entry, files untouched)
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -eq 0 ]]; then
  exec .venv/bin/python -m rag_brain.manage list
elif [[ "$1" == "--remove" ]]; then
  exec .venv/bin/python -m rag_brain.manage remove "$2"
fi
exec .venv/bin/python -m rag_brain.manage use "$1"

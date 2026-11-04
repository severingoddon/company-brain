#!/usr/bin/env bash
# Add a data source, index it and make it the ONLY active source in OpenClaw.
#   scripts/new_source.sh company ~/Downloads/hackathon-data "Company docs, meeting notes, FAQs"
#   scripts/new_source.sh company ~/Downloads/hackathon-data "..." --describe-images
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m rag_brain.manage add "$@"

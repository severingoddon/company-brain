#!/usr/bin/env bash
# Start the brain: check the Spark, start the OpenClaw gateway, open the Control UI in the browser.
set -uo pipefail

SPARK_API="${SPARK_API:-http://192.168.1.28:8000/v1}"

if curl -sf -m 5 "$SPARK_API/models" >/dev/null; then
  echo "Spark: $(curl -s -m 5 "$SPARK_API/models" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')"
else
  echo "Spark not reachable at $SPARK_API (company VPN? model not started?)." >&2
  echo "  ssh goddoni@192.168.1.28 'sparkrun run @eugr/qwen3.8-flash-next-nvfp4-solo'" >&2
fi

if openclaw gateway status 2>/dev/null | grep -q "Connectivity check: ok"; then
  echo "Gateway already running."
else
  echo "Starting gateway in the background (log: /tmp/openclaw-gateway.log) ..."
  OPENCLAW_TELEMETRY_ENABLED=false nohup openclaw gateway run >/tmp/openclaw-gateway.log 2>&1 &
  for _ in $(seq 1 30); do
    openclaw gateway status 2>/dev/null | grep -q "Connectivity check: ok" && break
    sleep 1
  done
fi

openclaw dashboard   # opens a one-time pairing link in the browser

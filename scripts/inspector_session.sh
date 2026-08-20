#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/logs/inspector"
mkdir -p "$LOG_DIR"

STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG_FILE="$LOG_DIR/${STAMP}_session.jsonl"

# Same launch style as scripts/smoke_tools.py
SERVER=(uv run python server.py)

call() {
  local method="$1"
  shift
  echo "{\"event\":\"start\",\"method\":\"$method\",\"ts\":\"$(date -Iseconds)\"}" >> "$LOG_FILE"

  npx --yes @modelcontextprotocol/inspector --cli \
    "${SERVER[@]}" \
    --method "$method" \
    --format json \
    "$@" >> "$LOG_FILE"

  echo "{\"event\":\"end\",\"method\":\"$method\",\"ts\":\"$(date -Iseconds)\"}" >> "$LOG_FILE"
}

echo "Logging session to: $LOG_FILE"

# 1) inventory
call tools/list

# 2) add tool calls below, one per line
# call tools/call --tool-name convert_mm_to_nm --tool-arg value_mm=1
# call tools/call --tool-name kicad_get_status

echo "Done. Session log: $LOG_FILE"
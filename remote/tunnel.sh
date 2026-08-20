#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Install cloudflared first: brew install cloudflared" >&2
  exit 1
fi
exec uv run python remote/http_server.py --tunnel "$@"

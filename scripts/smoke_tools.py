#!/usr/bin/env python3
"""Run every MCP tool via Inspector CLI and log one session file."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "inspector"
FIXTURES = ROOT / "tests" / "fixtures" / "tool_args.json"

SERVER_CMD = ["python", "server.py"]

# Do not call these unless --unsafe
UNSAFE_PREFIXES = (
    "board_create_",
    "board_move_",
    "board_remove_",
    "board_save",
    "board_set_",
    "board_import_",
    "board_export_",
    "board_refill_",
    "kicad_open_",
    "kicad_close_",
    "project_set_",
)


def inspector(method: str, extra: list[str] | None = None) -> dict:
    cmd = [
        "npx", "--yes", "@modelcontextprotocol/inspector", "--cli",
        *SERVER_CMD,
        "--method", method,
        "--format", "json",
        *(extra or []),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return {
            "ok": False,
            "stderr": proc.stderr[-4000:],
            "stdout": proc.stdout[-4000:],
            "returncode": proc.returncode,
        }
    try:
        return {"ok": True, "result": json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return {"ok": False, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}


def default_from_schema(schema: dict) -> dict:
    """Fill required (and optional) fields with type-based dummy values."""
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    args: dict = {}

    for name, spec in props.items():
        if "default" in spec:
            args[name] = spec["default"]
            continue
        if name not in required:
            continue

        if "enum" in spec and spec["enum"]:
            args[name] = spec["enum"][0]
            continue

        t = spec.get("type")
        if t == "string":
            args[name] = "test"
        elif t == "integer":
            args[name] = 0
        elif t == "number":
            args[name] = 1.0
        elif t == "boolean":
            args[name] = False
        elif t == "array":
            args[name] = []
        elif t == "object":
            args[name] = {}
        else:
            args[name] = None
    return args


def is_unsafe(name: str) -> bool:
    return name.startswith(UNSAFE_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unsafe", action="store_true", help="also run mutation/export tools")
    parser.add_argument("--only", help="substring filter, e.g. convert_ or board_get")
    args = parser.parse_args()

    fixtures = json.loads(FIXTURES.read_text()) if FIXTURES.exists() else {}

    listed = inspector("tools/list")
    if not listed.get("ok"):
        print("Failed to list tools:", listed, file=sys.stderr)
        return 1

    tools = listed["result"].get("result", listed["result"]).get("tools", [])
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_smoke.json"

    print(f"Found {len(tools)} tools")
    print(f"Logging to {log_path}")

    stats = {"ran": 0, "ok": 0, "failed": 0, "skipped": 0}

    with log_path.open("w") as fh:
            records = []
    for tool in tools:
        name = tool["name"]
        if args.only and args.only not in name:
            continue
        if is_unsafe(name) and not args.unsafe:
            stats["skipped"] += 1
            rec = {"tool": name, "status": "skipped", "reason": "unsafe"}
            records.append(rec)
            print(f"SKIP  {name}")
            continue

        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        call_args = default_from_schema(schema)
        call_args.update(fixtures.get(name, {}))

        extra = ["--tool-name", name, "--tool-args-json", json.dumps(call_args)]
        stats["ran"] += 1
        result = inspector("tools/call", extra)
        ok = bool(result.get("ok"))
        stats["ok" if ok else "failed"] += 1
        print(f"{'OK   ' if ok else 'FAIL '} {name}")

        records.append({
            "tool": name,
            "status": "ok" if ok else "failed",
            "args": call_args,
            "response": result,
        })

    log_path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_smoke.json"
    log_path.write_text(json.dumps({
        "stats": stats,
        "results": records,
    }, indent=2))

    print(stats)
    print(log_path)
    return 0 if stats["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
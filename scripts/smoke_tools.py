#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "inspector"
FIXTURES = ROOT / "tests" / "fixtures" / "tool_args.json"

SERVER_CMD = ["uv", "run", "python", "server.py"]

UNSAFE_PREFIXES = (
    "board_create_",
    "board_move_",
    "board_remove_",
    "board_save",
    "board_set_",
    "board_import_",
    "board_export_",
    "board_refill_",
    "board_clear_",
    "kicad_open_",
    "kicad_close_",
    "project_set_",
)

LIVE_LOOKUP = {
    "board_get_footprint": "reference",
    "board_get_net_info": "net_name",
    "board_get_items_by_net": "net_name",
    "board_get_zone": "name",
}

DUMMY_STRINGS = {"", "test", "unknown", "n/a"}

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

def inner_result(response: dict) -> dict:
    payload = response.get("result")
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("result", payload)
    return inner if isinstance(inner, dict) else {}

def structured_content(inner: dict) -> Any:
    if "structuredContent" in inner:
        return inner["structuredContent"]
    for item in inner.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text") or ""
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None

def call_succeeded(response: dict) -> tuple[bool, str]:
    """Return (ok, reason). Inspector process success is not enough."""
    if not response.get("ok"):
        return False, "inspector process failed"
    inner = inner_result(response)
    if inner.get("isError"):
        return False, "isError"
    sc = structured_content(inner)
    if isinstance(sc, dict):
        if sc.get("success") is False:
            return False, sc.get("message") or "success=false"
        message = str(sc.get("message") or "")
        if "not found" in message.lower():
            return False, message
        if "plugin identifier is invalid" in message.lower():
            return False, message
    if isinstance(sc, str) and "not found" in sc.lower():
        return False, sc[:200]
    stdout = str(response.get("stdout") or "")
    if "isError\":true" in stdout or "plugin identifier is invalid" in stdout:
        return False, "error payload in stdout"
    return True, ""

def default_from_schema(schema: dict) -> dict:
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

def _items_from_structured(sc: Any) -> list[dict]:
    if isinstance(sc, list):
        return [x for x in sc if isinstance(x, dict)]
    if not isinstance(sc, dict):
        return []
    if isinstance(sc.get("items"), list):
        return [x for x in sc["items"] if isinstance(x, dict)]
    if isinstance(sc.get("result"), list):
        return [x for x in sc["result"] if isinstance(x, dict)]
    return []

def harvest_live_fixtures(name: str, sc: Any, live: dict) -> None:
    """Fill lookup args from list/get tools that already ran."""
    items = _items_from_structured(sc)
    if name == "board_list_footprints":
        for item in items:
            ref = item.get("reference") or item.get("name")
            if isinstance(ref, str) and ref.strip() and ref.strip().lower() not in DUMMY_STRINGS:
                live["board_get_footprint"] = {"reference": ref.strip()}
                break
    elif name in ("board_list_nets", "board_get_net_info"):
        for item in items:
            net = item.get("net") or item.get("name")
            if isinstance(net, str) and net.strip() and net.strip().lower() not in DUMMY_STRINGS:
                live.setdefault("board_get_net_info", {"net_name": net.strip()})
                live.setdefault("board_get_items_by_net", {"net_name": net.strip()})
                break
        if isinstance(sc, dict):
            net = sc.get("data", {}).get("name") if isinstance(sc.get("data"), dict) else None
            if isinstance(net, str) and net.strip() and net.strip().lower() not in DUMMY_STRINGS:
                live.setdefault("board_get_net_info", {"net_name": net.strip()})
                live.setdefault("board_get_items_by_net", {"net_name": net.strip()})
    elif name in ("board_get_layers", "layer_list_copper"):
        fcu = None
        if isinstance(sc, dict):
            names = sc.get("layer_names") or {}
            for lid, lname in names.items():
                if str(lname) == "F.Cu":
                    fcu = int(lid)
                    break
            if fcu is None and sc.get("active_layer"):
                fcu = int(sc["active_layer"])
        for item in items:
            if item.get("canonical_name") == "F.Cu":
                fcu = int(item["layer_id"])
                break
        if fcu:
            live["layer_get_canonical_name"] = {"layer_id": fcu}
    elif name in ("board_list_zones", "board_list_zones_detailed"):
        for item in items:
            zname = item.get("name")
            if isinstance(zname, str) and zname.strip():
                live["board_get_zone"] = {"name": zname.strip()}
                break
    elif name == "project_get_info" and isinstance(sc, dict):
        path = str(sc.get("path") or "").strip()
        pname = str(sc.get("name") or "").strip()
        live["_has_saved_project"] = bool(path or pname)

def needs_live_skip(name: str, call_args: dict, live: dict) -> str | None:
    key = LIVE_LOOKUP.get(name)
    if key:
        value = call_args.get(key)
        if not isinstance(value, str) or value.strip().lower() in DUMMY_STRINGS:
            return f"no live {key} on the open board"
    if name == "project_expand_text_variables" and not live.get("_has_saved_project"):
        return "open board has no saved project path"
    return None

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unsafe", action="store_true", help="also run mutation/export tools")
    parser.add_argument("--only", help="substring filter, e.g. convert_ or board_get")
    parser.add_argument(
        "--probe-save",
        action="store_true",
        help="after the safe run, call board_save_as to a temp file (copy, not overwrite)",
    )
    args = parser.parse_args()

    fixtures = json.loads(FIXTURES.read_text()) if FIXTURES.exists() else {}
    live: dict[str, dict] = {}

    listed = inspector("tools/list")
    if not listed.get("ok"):
        print("Failed to list tools:", listed, file=sys.stderr)
        return 1

    tools = listed["result"].get("result", listed["result"]).get("tools", [])
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(tools)} tools")
    print(f"Fixtures: {FIXTURES if FIXTURES.exists() else '(missing)'}")

    stats = {"ran": 0, "ok": 0, "failed": 0, "skipped": 0}
    records: list[dict] = []

    for tool in tools:
        name = tool["name"]
        if args.only and args.only not in name:
            continue
        if is_unsafe(name) and not args.unsafe:
            stats["skipped"] += 1
            rec = {"tool": name, "status": "skipped", "reason": "unsafe"}
            records.append(rec)
            print(f"SKIP  {name} (unsafe)")
            continue

        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        call_args = default_from_schema(schema)
        call_args.update(fixtures.get(name, {}))
        call_args.update(live.get(name, {}))

        skip_reason = needs_live_skip(name, call_args, live)
        if skip_reason:
            stats["skipped"] += 1
            records.append({"tool": name, "status": "skipped", "reason": skip_reason, "args": call_args})
            print(f"SKIP  {name} ({skip_reason})")
            continue

        extra = ["--tool-name", name, "--tool-args-json", json.dumps(call_args)]
        stats["ran"] += 1
        result = inspector("tools/call", extra)
        ok, reason = call_succeeded(result)
        stats["ok" if ok else "failed"] += 1
        print(f"{'OK   ' if ok else 'FAIL '} {name}" + (f" — {reason}" if reason else ""))

        inner = inner_result(result)
        harvest_live_fixtures(name, structured_content(inner), live)

        records.append({
            "tool": name,
            "status": "ok" if ok else "failed",
            "args": call_args,
            "reason": reason or None,
            "response": result,
        })

    if args.probe_save:
        dest = str(Path(tempfile.mkdtemp(prefix="kicad-mcp-probe-")) / "probe.kicad_pcb")
        extra = [
            "--tool-name", "board_save_as",
            "--tool-args-json", json.dumps({"path": dest}),
        ]
        stats["ran"] += 1
        result = inspector("tools/call", extra)
        ok, reason = call_succeeded(result)
        stats["ok" if ok else "failed"] += 1
        print(f"{'OK   ' if ok else 'FAIL '} board_save_as (probe) -> {dest}" + (f" — {reason}" if reason else ""))
        records.append({
            "tool": "board_save_as",
            "status": "ok" if ok else "failed",
            "args": {"path": dest},
            "reason": reason or "probe-save",
            "response": result,
        })

    log_path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_smoke.json"
    log_path.write_text(json.dumps({
        "stats": stats,
        "live_fixtures": live,
        "results": records,
    }, indent=2))

    print(stats)
    print(log_path)
    return 0 if stats["failed"] == 0 else 2

if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

from kicad_mcp.connection import get_board_or_raise, get_kicad


class KiCadCliError(RuntimeError):
    pass


def kicad_cli_path() -> str:
    kicad = get_kicad()
    path = kicad.get_kicad_binary_path("kicad-cli")
    if not path:
        raise KiCadCliError("KiCad did not return a kicad-cli path.")
    return path


def _write_temp_board() -> tuple[tempfile.TemporaryDirectory, str]:
    board = get_board_or_raise()
    tmp = tempfile.TemporaryDirectory(prefix="kicad-mcp-export-")
    pcb_path = str(Path(tmp.name) / "board.kicad_pcb")
    Path(pcb_path).write_text(board.get_as_string(), encoding="utf-8")
    return tmp, pcb_path


def _layer_names(layer_ids: Optional[Sequence[int]]) -> list[str]:
    if not layer_ids:
        return []
    board = get_board_or_raise()
    names: list[str] = []
    for layer_id in layer_ids:
        try:
            names.append(str(board.get_layer_name(int(layer_id))))
        except Exception:
            names.append(str(layer_id))
    return names


def run_kicad_cli(args: list[str], timeout_s: int = 60) -> dict[str, Any]:
    cmd = [kicad_cli_path(), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        raise KiCadCliError(
            f"kicad-cli failed ({proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[-1500:]}"
        )
    return {
        "cmd": cmd,
        "stdout": (proc.stdout or "").strip()[-1500:],
        "stderr": (proc.stderr or "").strip()[-1500:],
    }


def export_with_cli(
    kind: str,
    output_path: str,
    layers: Optional[Sequence[int]] = None,
) -> dict[str, Any]:
    tmp, pcb_path = _write_temp_board()
    try:
        output_path = os.path.expanduser(output_path)
        if kind in ("gerbers", "drill"):
            os.makedirs(output_path, exist_ok=True)
            cmd = ["pcb", "export", kind, "-o", output_path]
            if kind == "gerbers":
                names = _layer_names(layers)
                if names:
                    cmd += ["--layers", ",".join(names)]
            cmd.append(pcb_path)
        elif kind in ("pos", "pdf"):
            parent = os.path.dirname(output_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            cmd = ["pcb", "export", kind, "-o", output_path, pcb_path]
        else:
            raise KiCadCliError(f"Unknown export kind: {kind}")

        details = run_kicad_cli(cmd)
        details["kind"] = kind
        details["board_snapshot"] = pcb_path
        return details
    finally:
        tmp.cleanup()

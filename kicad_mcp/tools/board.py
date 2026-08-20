from __future__ import annotations

from typing import Any, Optional

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from kicad_mcp.connection import get_kicad
from kicad_mcp.helpers.layers import nonzero_layer_ids

class BoardInfo(BaseModel):
    name: str
    available: bool
    copper_layer_count: Optional[int] = None
    title_block: Optional[dict[str, Any]] = None
    message: str = ""

class BoardItemSummary(BaseModel):
    count: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    message: str = ""

class LayerInfo(BaseModel):
    copper_layer_count: int
    enabled_layers: list[int] = Field(default_factory=list)
    visible_layers: list[int] = Field(default_factory=list)
    active_layer: Optional[int] = None
    layer_names: dict[int, str] = Field(default_factory=dict)

class ExportResult(BaseModel):
    success: bool
    output_path: str
    message: str
    details: Optional[dict[str, Any]] = None

class SimpleResult(BaseModel):
    success: bool
    message: str
    data: Optional[dict[str, Any]] = None

def _get_board():
    kicad = get_kicad()
    board = kicad.get_board()
    if board is None:
        raise RuntimeError("No board is currently open in KiCad.")
    return board

def _field_text(obj: Any, field_name: str) -> Optional[str]:
    try:
        field = getattr(obj, field_name, None)
        if field is None:
            return None
        if hasattr(field, "value") and not callable(field.value):
            val = field.value
            if isinstance(val, str):
                return val
        text = getattr(field, "text", None)
        if text is not None:
            val = getattr(text, "value", None)
            if val is not None:
                return str(val)
    except Exception:
        return None
    return None

def _title_block_dict(tb: Any) -> dict[str, Any]:
    if tb is None:
        return {}
    comments: dict[int, str] = {}
    try:
        raw = getattr(tb, "comments", None)
        if isinstance(raw, dict):
            comments = {int(k): str(v) for k, v in raw.items() if v}
    except Exception:
        pass
    return {
        "title": getattr(tb, "title", None) or None,
        "date": getattr(tb, "date", None) or None,
        "revision": getattr(tb, "revision", None) or None,
        "company": getattr(tb, "company", None) or None,
        "comments": comments,
    }

def _safe_summary(obj: Any, max_fields: int = 12) -> dict[str, Any]:
    """Best-effort conversion of kicad objects into plain dicts for the LLM."""
    if obj is None:
        return {}
    data: dict[str, Any] = {}
    ref = _field_text(obj, "reference_field")
    if ref:
        data["reference"] = ref
    value = _field_text(obj, "value_field")
    if value:
        data["value"] = value
    for attr in ("name", "net", "layer", "position", "orientation", "uuid", "kiid", "id"):
        if attr in data or not hasattr(obj, attr):
            continue
        try:
            val = getattr(obj, attr)
            if attr == "net" and val is not None:
                data["net"] = getattr(val, "name", str(val))
            elif isinstance(val, (int, float, bool, str, type(None))):
                data[attr] = val
            else:
                data[attr] = str(val)
        except Exception:
            pass
    if not data:
        data["repr"] = str(obj)[:300]
    return data

def register_board_tools(mcp: MCPServer) -> None:

    @mcp.tool()
    def board_get_info() -> BoardInfo:
        """
        Get high-level information about the currently open board.

        Returns the board filename, copper layer count, title block data,
        and basic status. Call this early to understand the current design.
        """
        try:
            board = _get_board()
            title = None
            try:
                title = _title_block_dict(board.get_title_block_info())
            except Exception:
                pass

            copper = None
            try:
                copper = board.get_copper_layer_count()
            except Exception:
                pass

            return BoardInfo(
                name=getattr(board, "name", "unknown"),
                available=True,
                copper_layer_count=copper,
                title_block=title,
                message="Board is open and accessible.",
            )
        except Exception as e:
            return BoardInfo(name="", available=False, message=str(e))

    @mcp.tool()
    def board_list_footprints(limit: int = 50) -> BoardItemSummary:
        """
        List footprints (components) currently on the board.

        Returns a compact summary (reference, value, etc.) so the model
        can reason about the design without drowning in data.
        Use `limit` to control how many are returned.
        """
        board = _get_board()
        fps = list(board.get_footprints())
        items = [_safe_summary(fp) for fp in fps[:limit]]
        return BoardItemSummary(
            count=len(fps),
            items=items,
            message=f"Found {len(fps)} footprints (showing up to {limit}).",
        )

    @mcp.tool()
    def board_list_nets(netclass_filter: Optional[str] = None, limit: int = 100) -> BoardItemSummary:
        """
        List nets on the board, optionally filtered by net class name.
        """
        board = _get_board()
        nets = list(board.get_nets(netclass_filter))
        items = [_safe_summary(n) for n in nets[:limit]]
        return BoardItemSummary(
            count=len(nets),
            items=items,
            message=f"Found {len(nets)} nets.",
        )

    @mcp.tool()
    def board_list_tracks(limit: int = 50) -> BoardItemSummary:
        """List tracks and arc tracks on the board."""
        board = _get_board()
        tracks = list(board.get_tracks())
        items = [_safe_summary(t) for t in tracks[:limit]]
        return BoardItemSummary(count=len(tracks), items=items,
                                message=f"Found {len(tracks)} tracks.")

    @mcp.tool()
    def board_list_vias(limit: int = 50) -> BoardItemSummary:
        """List vias on the board."""
        board = _get_board()
        vias = list(board.get_vias())
        items = [_safe_summary(v) for v in vias[:limit]]
        return BoardItemSummary(count=len(vias), items=items,
                                message=f"Found {len(vias)} vias.")

    @mcp.tool()
    def board_list_zones(limit: int = 30) -> BoardItemSummary:
        """List zones (copper pours, rule areas, etc.) on the board."""
        board = _get_board()
        zones = list(board.get_zones())
        items = [_safe_summary(z) for z in zones[:limit]]
        return BoardItemSummary(count=len(zones), items=items,
                                message=f"Found {len(zones)} zones.")

    @mcp.tool()
    def board_get_items_by_net(
        net_name: str,
        types: Optional[list[int]] = None,
        limit: int = 50,
    ) -> BoardItemSummary:
        """
        Get all items belonging to a specific net (by name).

        Useful for answering "what is connected to NET_XXX?" or
        inspecting a particular signal.
        """
        board = _get_board()
        nets = {getattr(n, "name", str(n)): n for n in board.get_nets()}
        if net_name not in nets:
            return BoardItemSummary(count=0, message=f"Net '{net_name}' not found.")

        items_raw = list(board.get_items_by_net(nets[net_name], types))
        items = [_safe_summary(i) for i in items_raw[:limit]]
        return BoardItemSummary(
            count=len(items_raw),
            items=items,
            message=f"Found {len(items_raw)} items on net '{net_name}'.",
        )

    @mcp.tool()
    def board_get_layers() -> LayerInfo:
        """
        Get copper layer count, enabled layers, visible layers,
        active layer, and human-readable layer names.
        """
        board = _get_board()
        copper = board.get_copper_layer_count()
        enabled = nonzero_layer_ids(board.get_enabled_layers())
        visible = nonzero_layer_ids(board.get_visible_layers())
        active = board.get_active_layer()
        try:
            active = int(active) if active else None
        except Exception:
            pass

        names = {}
        for layer in enabled:
            try:
                names[layer] = board.get_layer_name(layer)
            except Exception:
                names[layer] = str(layer)

        return LayerInfo(
            copper_layer_count=copper,
            enabled_layers=enabled,
            visible_layers=visible,
            active_layer=active,
            layer_names=names,
        )

    @mcp.tool()
    def board_get_stackup() -> dict[str, Any]:
        """
        Retrieve the full board stackup information
        (dielectrics, copper layers, materials, thicknesses, etc.).
        """
        board = _get_board()
        stackup = board.get_stackup()
        # Best-effort serialization
        return {"stackup": _safe_summary(stackup), "raw": str(stackup)[:2000]}

    @mcp.tool()
    def board_refill_zones(block: bool = True) -> SimpleResult:
        """
        Refill all zones (copper pours) on the board.

        This is a common operation after moving footprints or changing nets.
        When block=True the call waits until the refill finishes.
        """
        board = _get_board()
        board.refill_zones(block=block)
        return SimpleResult(success=True, message="Zone refill started/completed.")

    @mcp.tool()
    def board_save() -> SimpleResult:
        """Save the current board to its existing file."""
        try:
            board = _get_board()
            board.save()
            return SimpleResult(success=True, message="Board saved.")
        except Exception as e:
            return SimpleResult(success=False, message=str(e))

    @mcp.tool()
    def board_save_as(path: str, overwrite: bool = False) -> SimpleResult:
        """
        Save a copy of the current board to a new path.

        The file must not already exist unless overwrite=True.
        """
        try:
            board = _get_board()
            board.save_as(path, overwrite=overwrite)
            return SimpleResult(success=True, message=f"Board saved as {path}.")
        except Exception as e:
            return SimpleResult(success=False, message=str(e))

    @mcp.tool()
    def board_import_netlist(
        netlist_path: str,
        dry_run: bool = False,
        delete_extra_footprints: bool = True,
        update_footprints: bool = True,
    ) -> SimpleResult:
        """
        Import a netlist (usually exported from the schematic) into the board.

        Use dry_run=True to preview changes without modifying the board.
        """
        board = _get_board()
        result = board.import_netlist(
            netlist_path,
            dry_run=dry_run,
            delete_extra_footprints=delete_extra_footprints,
            update_footprints=update_footprints,
        )
        return SimpleResult(
            success=True,
            message="Netlist import finished.",
            data=_safe_summary(result),
        )

    def _export(kind: str, output_path: str, layers: Optional[list[int]] = None) -> ExportResult:
        from kicad_mcp.helpers.cli import KiCadCliError, export_with_cli

        board = _get_board()
        ipc_name = {
            "gerbers": "export_gerbers",
            "drill": "export_drill",
            "pos": "export_position",
            "pdf": "export_pdf",
        }[kind]
        if hasattr(board, ipc_name):
            try:
                method = getattr(board, ipc_name)
                if kind == "gerbers":
                    result = method(output_path, layers or [])
                elif kind == "drill":
                    result = method(output_path)
                else:
                    result = method(output_path)
                return ExportResult(
                    success=True,
                    output_path=output_path,
                    message=f"{kind} exported via IPC.",
                    details=_safe_summary(result),
                )
            except Exception as ipc_error:
                ipc_msg = str(ipc_error)
        else:
            ipc_msg = f"Board.{ipc_name} is not in this kicad-python"

        try:
            details = export_with_cli(kind, output_path, layers)
            details["ipc"] = ipc_msg
            return ExportResult(
                success=True,
                output_path=output_path,
                message=f"{kind} exported via kicad-cli (IPC plotting is KiCad 11+).",
                details=details,
            )
        except KiCadCliError as e:
            return ExportResult(
                success=False,
                output_path=output_path,
                message=str(e),
                details={"ipc": ipc_msg},
            )
        except Exception as e:
            return ExportResult(
                success=False,
                output_path=output_path,
                message=f"Export failed: {e}",
                details={"ipc": ipc_msg},
            )

    @mcp.tool()
    def board_export_gerbers(
        output_path: str,
        layers: Optional[list[int]] = None,
    ) -> ExportResult:
        """
        Export Gerber files.

        KiCad 9/10 has no IPC plotting; this calls kicad-cli on a snapshot of
        the open board. `output_path` is a directory. `layers` is optional
        (IPC layer IDs from board_get_layers; F.Cu is 3, not 0).
        """
        return _export("gerbers", output_path, layers)

    @mcp.tool()
    def board_export_drill(output_path: str, format: int = 1) -> ExportResult:
        """Export NC drill files via kicad-cli (IPC plotting is KiCad 11+). `output_path` is a directory."""
        return _export("drill", output_path)

    @mcp.tool()
    def board_export_position_file(
        output_path: str,
    ) -> ExportResult:
        """Export pick-and-place file via kicad-cli (IPC plotting is KiCad 11+)."""
        return _export("pos", output_path)

    @mcp.tool()
    def board_export_pdf(output_path: str) -> ExportResult:
        """Export a PDF plot via kicad-cli (IPC plotting is KiCad 11+)."""
        return _export("pdf", output_path)

    @mcp.tool()
    def board_get_selection() -> BoardItemSummary:
        """Return the items currently selected in the KiCad PCB editor."""
        board = _get_board()
        sel = list(board.get_selection())
        items = [_safe_summary(i) for i in sel]
        return BoardItemSummary(
            count=len(sel),
            items=items,
            message=f"{len(sel)} item(s) selected.",
        )

    @mcp.tool()
    def board_clear_selection() -> SimpleResult:
        """Clear the current selection in the PCB editor."""
        board = _get_board()
        board.clear_selection()
        return SimpleResult(success=True, message="Selection cleared.")

# kicad_mcp/tools/board.py
"""
MCP tools derived from kipy.board.Board.

Focus: high-signal operations an agent actually needs.
Low-level 1:1 wrappers are avoided on purpose.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Sequence

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from kicad_mcp.connection import get_kicad


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class BoardInfo(BaseModel):
    name: str
    available: bool
    copper_layer_count: Optional[int] = None
    title_block: Optional[dict[str, Any]] = None
    message: str = ""


class BoardItemSummary(BaseModel):
    """Lightweight summary so we don't dump huge objects into context."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_board():
    kicad = get_kicad()
    board = kicad.get_board()
    if board is None:
        raise RuntimeError("No board is currently open in KiCad.")
    return board


def _safe_summary(obj: Any, max_fields: int = 12) -> dict[str, Any]:
    """Best-effort conversion of kicad objects into plain dicts for the LLM."""
    if obj is None:
        return {}
    data = {}
    # Common useful attributes across many board items
    for attr in ("name", "net", "layer", "position", "orientation", "uuid", "kiid", "value", "reference"):
        if hasattr(obj, attr):
            try:
                val = getattr(obj, attr)
                data[attr] = str(val) if not isinstance(val, (int, float, bool, str, type(None))) else val
            except Exception:
                pass
    # Fallback
    if not data:
        data["repr"] = str(obj)[:300]
    return data


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def register_board_tools(mcp: MCPServer) -> None:

    # ------------------------------------------------------------------
    # 1. Board overview
    # ------------------------------------------------------------------
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
                tb = board.get_title_block_info()
                title = _safe_summary(tb)
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

    # ------------------------------------------------------------------
    # 2. Item discovery (the most important group)
    # ------------------------------------------------------------------
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
        # First find the Net object
        nets = {str(n): n for n in board.get_nets()}
        if net_name not in nets:
            return BoardItemSummary(count=0, message=f"Net '{net_name}' not found.")

        items_raw = list(board.get_items_by_net(nets[net_name], types))
        items = [_safe_summary(i) for i in items_raw[:limit]]
        return BoardItemSummary(
            count=len(items_raw),
            items=items,
            message=f"Found {len(items_raw)} items on net '{net_name}'.",
        )

    # ------------------------------------------------------------------
    # 3. Layers & Stackup
    # ------------------------------------------------------------------
    @mcp.tool()
    def board_get_layers() -> LayerInfo:
        """
        Get copper layer count, enabled layers, visible layers,
        active layer, and human-readable layer names.
        """
        board = _get_board()
        copper = board.get_copper_layer_count()
        enabled = list(board.get_enabled_layers())
        visible = list(board.get_visible_layers())
        active = board.get_active_layer()

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

    # ------------------------------------------------------------------
    # 4. Important operations
    # ------------------------------------------------------------------
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
        board = _get_board()
        board.save()
        return SimpleResult(success=True, message="Board saved.")

    @mcp.tool()
    def board_save_as(path: str) -> SimpleResult:
        """Save the current board to a new path."""
        board = _get_board()
        board.save_as(path)
        return SimpleResult(success=True, message=f"Board saved as {path}.")

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

    # ------------------------------------------------------------------
    # 5. Exports (most common ones + a flexible entry point)
    # ------------------------------------------------------------------
    @mcp.tool()
    def board_export_gerbers(output_path: str, layers: list[int]) -> ExportResult:
        """
        Export Gerber files for the given copper/technical layers.

        `layers` should be a list of layer IDs (use board_get_layers first
        to discover valid IDs).
        """
        board = _get_board()
        result = board.export_gerbers(output_path, layers)
        return ExportResult(
            success=True,
            output_path=output_path,
            message="Gerbers exported.",
            details=_safe_summary(result),
        )

    @mcp.tool()
    def board_export_drill(output_path: str, format: int = 1) -> ExportResult:
        """Export NC drill files."""
        board = _get_board()
        result = board.export_drill(output_path, format=format)
        return ExportResult(
            success=True,
            output_path=output_path,
            message="Drill files exported.",
            details=_safe_summary(result),
        )

    @mcp.tool()
    def board_export_position_file(
        output_path: str,
    ) -> ExportResult:
        """Export pick-and-place (position) file."""
        board = _get_board()
        result = board.export_position(output_path)
        return ExportResult(
            success=True,
            output_path=output_path,
            message="Position file exported.",
            details=_safe_summary(result),
        )

    @mcp.tool()
    def board_export_pdf(output_path: str) -> ExportResult:
        """Export a PDF plot of the board."""
        board = _get_board()
        result = board.export_pdf(output_path)
        return ExportResult(
            success=True,
            output_path=output_path,
            message="PDF exported.",
            details=_safe_summary(result),
        )

    # ------------------------------------------------------------------
    # 6. Selection (kept small – useful for interactive agent workflows)
    # ------------------------------------------------------------------
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
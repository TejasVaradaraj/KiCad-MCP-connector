# kicad_mcp/tools/board.py
"""
MCP tools derived from kipy.board.Board.

Focus: high-signal operations an agent actually needs.
Low-level 1:1 wrappers are avoided on purpose.
"""

from __future__ import annotations

import os
import math
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


class NetLengthReport(BaseModel):
    net_name: str
    found: bool
    segment_count: int = 0
    via_count: int = 0
    total_track_length_mm: float = 0.0
    per_layer_length_mm: dict[str, float] = Field(default_factory=dict)
    message: str = ""


class BoardBBox(BaseModel):
    found: bool
    min_x_mm: Optional[float] = None
    min_y_mm: Optional[float] = None
    max_x_mm: Optional[float] = None
    max_y_mm: Optional[float] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    sampled_points: int = 0
    message: str = ""


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


def _nm_to_mm(value_nm: int | float) -> float:
    return float(value_nm) / 1_000_000.0


def _mm_to_nm(value_mm: int | float) -> int:
    return int(round(float(value_mm) * 1_000_000.0))


def _footprint_reference(fp: Any) -> Optional[str]:
    try:
        ref_field = getattr(fp, "reference_field", None)
        if ref_field is not None:
            ref = getattr(ref_field, "value", None)
            if ref is not None:
                return str(ref)
    except Exception:
        pass
    ref = getattr(fp, "reference", None)
    return str(ref) if ref is not None else None


def _footprint_value(fp: Any) -> Optional[str]:
    try:
        value_field = getattr(fp, "value_field", None)
        if value_field is not None:
            value = getattr(value_field, "value", None)
            if value is not None:
                return str(value)
    except Exception:
        pass
    value = getattr(fp, "value", None)
    return str(value) if value is not None else None


def _footprint_position_mm(fp: Any) -> Optional[dict[str, float]]:
    pos = getattr(fp, "position", None)
    if pos is None:
        return None
    try:
        return {
            "x": _nm_to_mm(getattr(pos, "x", 0)),
            "y": _nm_to_mm(getattr(pos, "y", 0)),
        }
    except Exception:
        return None


def _net_name(net: Any) -> str:
    name = getattr(net, "name", None)
    if name is not None:
        return str(name)
    return str(net)


def _track_length_nm(track: Any) -> Optional[float]:
    # Prefer API-provided length when available (works for lines and arcs in many builds).
    try:
        length_fn = getattr(track, "length", None)
        if callable(length_fn):
            return float(length_fn())
    except Exception:
        pass

    # Fallback for simple segments that expose start/end points.
    start = getattr(track, "start", None)
    end = getattr(track, "end", None)
    if start is None or end is None:
        return None

    try:
        dx = float(getattr(end, "x", 0)) - float(getattr(start, "x", 0))
        dy = float(getattr(end, "y", 0)) - float(getattr(start, "y", 0))
        return math.hypot(dx, dy)
    except Exception:
        return None


def _find_footprint_by_reference(board: Any, reference: str) -> Optional[Any]:
    ref_wanted = reference.strip()
    for fp in board.get_footprints():
        ref = _footprint_reference(fp)
        if ref == ref_wanted:
            return fp
    return None


def _set_xy_mm(obj: Any, x_mm: float, y_mm: float) -> bool:
    pos = getattr(obj, "position", None)
    if pos is None:
        return False
    try:
        setattr(pos, "x", _mm_to_nm(x_mm))
        setattr(pos, "y", _mm_to_nm(y_mm))
        obj.position = pos
        return True
    except Exception:
        return False


def _numeric_attr(obj: Any, attr: str) -> Optional[float]:
    try:
        value = getattr(obj, attr, None)
        if value is None:
            return None
        if callable(value):
            value = value()
        return float(value)
    except Exception:
        return None


def _item_net_name(item: Any) -> Optional[str]:
    net = getattr(item, "net", None)
    if net is None:
        return None
    return _net_name(net)


def _vector_xy_mm(vec: Any) -> Optional[tuple[float, float]]:
    if vec is None:
        return None
    try:
        x = _nm_to_mm(getattr(vec, "x"))
        y = _nm_to_mm(getattr(vec, "y"))
        return (x, y)
    except Exception:
        return None


def _item_points_mm(item: Any) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    # Common geometry attributes across board items.
    for attr in ("position", "start", "end", "center", "top_left", "bottom_right"):
        p = _vector_xy_mm(getattr(item, attr, None))
        if p is not None:
            points.append(p)

    # For vias and similar circular objects, include rough extents if diameter is available.
    pos = getattr(item, "position", None)
    diam_nm = _numeric_attr(item, "diameter")
    if pos is not None and diam_nm is not None:
        center = _vector_xy_mm(pos)
        if center is not None:
            r = _nm_to_mm(diam_nm) / 2.0
            points.extend(
                [
                    (center[0] - r, center[1]),
                    (center[0] + r, center[1]),
                    (center[0], center[1] - r),
                    (center[0], center[1] + r),
                ]
            )

    return points


def _default_fab_layers(board: Any) -> list[int]:
    """Pick a practical default set of plot layers for fabrication outputs."""
    wanted = {"F.Cu", "B.Cu", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "Edge.Cuts"}
    selected: list[int] = []
    for layer_id in board.get_enabled_layers():
        try:
            name = board.get_layer_name(layer_id)
        except Exception:
            continue
        if name in wanted:
            selected.append(layer_id)

    if selected:
        return selected

    # Fallback if names cannot be resolved in this environment.
    enabled = list(board.get_enabled_layers())
    return enabled[:2] if len(enabled) >= 2 else enabled


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
    def board_find_footprints(
        reference_contains: Optional[str] = None,
        value_contains: Optional[str] = None,
        layer: Optional[int] = None,
        locked: Optional[bool] = None,
        min_x_mm: Optional[float] = None,
        max_x_mm: Optional[float] = None,
        min_y_mm: Optional[float] = None,
        max_y_mm: Optional[float] = None,
        limit: int = 100,
    ) -> BoardItemSummary:
        """
        Find footprints using practical filters (reference/value text, layer,
        lock state, and optional position bounding box in millimeters).

        This is useful when you need targeted component sets without pulling
        every footprint and filtering client-side.
        """
        board = _get_board()

        ref_query = reference_contains.lower() if reference_contains is not None else None
        value_query = value_contains.lower() if value_contains is not None else None

        matched: list[dict[str, Any]] = []
        total_matches = 0
        for fp in board.get_footprints():
            ref = _footprint_reference(fp)
            val = _footprint_value(fp)
            fp_layer = getattr(fp, "layer", None)
            fp_locked = getattr(fp, "locked", None)
            pos_mm = _footprint_position_mm(fp)

            if ref_query is not None and (ref is None or ref_query not in ref.lower()):
                continue
            if value_query is not None and (val is None or value_query not in val.lower()):
                continue
            if layer is not None and fp_layer != layer:
                continue
            if locked is not None and fp_locked != locked:
                continue

            if pos_mm is not None:
                x_mm = pos_mm["x"]
                y_mm = pos_mm["y"]
                if min_x_mm is not None and x_mm < min_x_mm:
                    continue
                if max_x_mm is not None and x_mm > max_x_mm:
                    continue
                if min_y_mm is not None and y_mm < min_y_mm:
                    continue
                if max_y_mm is not None and y_mm > max_y_mm:
                    continue

            total_matches += 1
            if len(matched) < limit:
                matched.append(
                    {
                        "id": str(getattr(fp, "id", "")),
                        "reference": ref,
                        "value": val,
                        "layer": fp_layer,
                        "locked": fp_locked,
                        "position_mm": pos_mm,
                    }
                )

        return BoardItemSummary(
            count=total_matches,
            items=matched,
            message=f"Found {total_matches} footprint(s) (showing up to {limit}).",
        )

    @mcp.tool()
    def board_align_footprints(
        references: list[str],
        mode: Literal["left", "right", "top", "bottom", "center_x", "center_y"],
        spacing_mm: Optional[float] = None,
    ) -> SimpleResult:
        """
        Align multiple footprints along one axis, optionally distributing
        them with uniform spacing along the perpendicular axis.

        Notes
        -----
        - `left`, `right`, `center_x` align X positions.
        - `top`, `bottom`, `center_y` align Y positions.
        - When spacing_mm is provided, the set is ordered by its current
          perpendicular coordinate and then laid out with fixed spacing.
        """
        if len(references) < 2:
            return SimpleResult(
                success=False,
                message="Provide at least two footprint references.",
            )

        board = _get_board()

        found: list[tuple[str, Any, dict[str, float]]] = []
        missing: list[str] = []
        for ref in references:
            fp = _find_footprint_by_reference(board, ref)
            if fp is None:
                missing.append(ref)
                continue
            pos = _footprint_position_mm(fp)
            if pos is None:
                missing.append(ref)
                continue
            found.append((ref, fp, pos))

        if len(found) < 2:
            return SimpleResult(
                success=False,
                message="Could not resolve enough footprints with valid positions.",
                data={"missing_or_invalid": missing},
            )

        xs = [item[2]["x"] for item in found]
        ys = [item[2]["y"] for item in found]

        if mode == "left":
            anchor_x = min(xs)
        elif mode == "right":
            anchor_x = max(xs)
        elif mode == "center_x":
            anchor_x = sum(xs) / len(xs)
        else:
            anchor_x = None

        if mode == "bottom":
            anchor_y = min(ys)
        elif mode == "top":
            anchor_y = max(ys)
        elif mode == "center_y":
            anchor_y = sum(ys) / len(ys)
        else:
            anchor_y = None

        updated_items: list[Any] = []
        moved: list[dict[str, Any]] = []

        if spacing_mm is not None and spacing_mm < 0:
            return SimpleResult(success=False, message="spacing_mm must be non-negative.")

        if mode in ("left", "right", "center_x"):
            ordered = sorted(found, key=lambda it: it[2]["y"])
            start_y = ordered[0][2]["y"]
            for idx, (ref, fp, pos) in enumerate(ordered):
                new_x = anchor_x if anchor_x is not None else pos["x"]
                new_y = pos["y"] if spacing_mm is None else start_y + idx * spacing_mm
                if not _set_xy_mm(fp, new_x, new_y):
                    continue
                updated_items.append(fp)
                moved.append(
                    {
                        "reference": ref,
                        "from_mm": {"x": pos["x"], "y": pos["y"]},
                        "to_mm": {"x": new_x, "y": new_y},
                    }
                )
        else:
            ordered = sorted(found, key=lambda it: it[2]["x"])
            start_x = ordered[0][2]["x"]
            for idx, (ref, fp, pos) in enumerate(ordered):
                new_x = pos["x"] if spacing_mm is None else start_x + idx * spacing_mm
                new_y = anchor_y if anchor_y is not None else pos["y"]
                if not _set_xy_mm(fp, new_x, new_y):
                    continue
                updated_items.append(fp)
                moved.append(
                    {
                        "reference": ref,
                        "from_mm": {"x": pos["x"], "y": pos["y"]},
                        "to_mm": {"x": new_x, "y": new_y},
                    }
                )

        if not updated_items:
            return SimpleResult(
                success=False,
                message="No footprints were updated.",
                data={"missing_or_invalid": missing},
            )

        commit = board.begin_commit()
        try:
            for fp in updated_items:
                board.update_items(fp)
            board.push_commit(commit, f"Align {len(updated_items)} footprint(s)")
        except Exception as e:
            board.drop_commit(commit)
            return SimpleResult(
                success=False,
                message=f"Failed to align footprints: {e}",
                data={"missing_or_invalid": missing},
            )

        return SimpleResult(
            success=True,
            message=f"Aligned {len(updated_items)} footprint(s) with mode '{mode}'.",
            data={
                "mode": mode,
                "spacing_mm": spacing_mm,
                "moved_count": len(updated_items),
                "missing_or_invalid": missing,
                "moved": moved,
            },
        )

    @mcp.tool()
    def board_distribute_footprints(
        references: list[str],
        axis: Literal["x", "y"],
        spacing_mode: Literal["fixed", "even"] = "even",
        spacing_mm: Optional[float] = None,
    ) -> SimpleResult:
        """
        Distribute footprints along one axis.

        Parameters
        ----------
        references:
            Footprint references (e.g. ["R1", "R2", "R3"]).
        axis:
            Axis to distribute on: "x" or "y".
        spacing_mode:
            - "even": keep first/last fixed, spread others uniformly between them.
            - "fixed": use explicit spacing_mm from the first item onward.
        spacing_mm:
            Required when spacing_mode="fixed".
        """
        if len(references) < 2:
            return SimpleResult(
                success=False,
                message="Provide at least two footprint references.",
            )

        if spacing_mode == "fixed" and spacing_mm is None:
            return SimpleResult(
                success=False,
                message="spacing_mm is required when spacing_mode='fixed'.",
            )
        if spacing_mm is not None and spacing_mm < 0:
            return SimpleResult(success=False, message="spacing_mm must be non-negative.")

        board = _get_board()
        found: list[tuple[str, Any, dict[str, float]]] = []
        missing: list[str] = []
        for ref in references:
            fp = _find_footprint_by_reference(board, ref)
            if fp is None:
                missing.append(ref)
                continue
            pos = _footprint_position_mm(fp)
            if pos is None:
                missing.append(ref)
                continue
            found.append((ref, fp, pos))

        if len(found) < 2:
            return SimpleResult(
                success=False,
                message="Could not resolve enough footprints with valid positions.",
                data={"missing_or_invalid": missing},
            )

        axis_key = "x" if axis == "x" else "y"
        orth_key = "y" if axis == "x" else "x"
        ordered = sorted(found, key=lambda it: it[2][axis_key])

        start = ordered[0][2][axis_key]
        end = ordered[-1][2][axis_key]
        count = len(ordered)

        updated_items: list[Any] = []
        moved: list[dict[str, Any]] = []

        for idx, (ref, fp, pos) in enumerate(ordered):
            old_axis = pos[axis_key]
            old_orth = pos[orth_key]

            if spacing_mode == "fixed":
                new_axis = start + idx * float(spacing_mm)
            else:
                if count <= 2:
                    new_axis = old_axis
                else:
                    step = (end - start) / float(count - 1)
                    new_axis = start + idx * step

            new_x = new_axis if axis_key == "x" else old_orth
            new_y = new_axis if axis_key == "y" else old_orth

            if not _set_xy_mm(fp, new_x, new_y):
                continue

            updated_items.append(fp)
            moved.append(
                {
                    "reference": ref,
                    "from_mm": {"x": pos["x"], "y": pos["y"]},
                    "to_mm": {"x": new_x, "y": new_y},
                }
            )

        if not updated_items:
            return SimpleResult(
                success=False,
                message="No footprints were updated.",
                data={"missing_or_invalid": missing},
            )

        commit = board.begin_commit()
        try:
            for fp in updated_items:
                board.update_items(fp)
            board.push_commit(commit, f"Distribute {len(updated_items)} footprint(s)")
        except Exception as e:
            board.drop_commit(commit)
            return SimpleResult(
                success=False,
                message=f"Failed to distribute footprints: {e}",
                data={"missing_or_invalid": missing},
            )

        return SimpleResult(
            success=True,
            message=f"Distributed {len(updated_items)} footprint(s) on {axis.upper()} axis.",
            data={
                "axis": axis,
                "spacing_mode": spacing_mode,
                "spacing_mm": spacing_mm,
                "moved_count": len(updated_items),
                "missing_or_invalid": missing,
                "moved": moved,
            },
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

    @mcp.tool()
    def board_get_net_length_report(
        net_name: str,
        include_vias: bool = True,
    ) -> NetLengthReport:
        """
        Report total copper track length for a net, with a per-layer split.

        Length is derived from track/arc segment geometry where available.
        Vias are counted separately (optional) but do not contribute to length.
        """
        board = _get_board()
        nets = {_net_name(n): n for n in board.get_nets()}
        if net_name not in nets:
            return NetLengthReport(
                net_name=net_name,
                found=False,
                message=f"Net '{net_name}' not found.",
            )

        total_length_mm = 0.0
        segment_count = 0
        via_count = 0
        per_layer_length_mm: dict[str, float] = {}

        for item in board.get_items_by_net(nets[net_name]):
            item_type_name = type(item).__name__.lower()

            if "via" in item_type_name:
                if include_vias:
                    via_count += 1
                continue

            length_nm = _track_length_nm(item)
            if length_nm is None:
                continue

            length_mm = _nm_to_mm(length_nm)
            total_length_mm += length_mm
            segment_count += 1

            layer = getattr(item, "layer", None)
            if layer is None:
                layer_key = "unknown"
            else:
                try:
                    layer_key = board.get_layer_name(layer)
                except Exception:
                    layer_key = str(layer)
            per_layer_length_mm[layer_key] = per_layer_length_mm.get(layer_key, 0.0) + length_mm

        return NetLengthReport(
            net_name=net_name,
            found=True,
            segment_count=segment_count,
            via_count=via_count,
            total_track_length_mm=total_length_mm,
            per_layer_length_mm=per_layer_length_mm,
            message=f"Computed length from {segment_count} segment(s).",
        )

    @mcp.tool()
    def board_check_min_width_clearance(
        min_width_mm: float,
        min_clearance_mm: Optional[float] = None,
        net_name: Optional[str] = None,
        layer: Optional[int] = None,
        limit: int = 200,
    ) -> BoardItemSummary:
        """
        Check tracks (and vias for clearance when available) against minimum
        width/clearance constraints.

        This is a fast, in-session sanity check and is not a full DRC
        replacement. Use KiCad DRC for complete rule evaluation.
        """
        board = _get_board()

        width_violations = 0
        clearance_violations = 0
        violations: list[dict[str, Any]] = []

        min_width_nm = min_width_mm * 1_000_000.0
        min_clearance_nm = (
            min_clearance_mm * 1_000_000.0 if min_clearance_mm is not None else None
        )

        def _append_violation(entry: dict[str, Any]) -> None:
            if len(violations) < limit:
                violations.append(entry)

        for track in board.get_tracks():
            item_layer = getattr(track, "layer", None)
            item_net = _item_net_name(track)

            if layer is not None and item_layer != layer:
                continue
            if net_name is not None and item_net != net_name:
                continue

            width_nm = _numeric_attr(track, "width")
            if width_nm is not None and width_nm < min_width_nm:
                width_violations += 1
                _append_violation(
                    {
                        "rule": "min_width",
                        "item_type": type(track).__name__,
                        "id": str(getattr(track, "id", "")),
                        "net": item_net,
                        "layer": item_layer,
                        "width_mm": _nm_to_mm(width_nm),
                        "required_min_width_mm": min_width_mm,
                    }
                )

            if min_clearance_nm is not None:
                clearance_nm = None
                for attr in ("clearance", "local_clearance", "own_clearance"):
                    clearance_nm = _numeric_attr(track, attr)
                    if clearance_nm is not None:
                        break
                if clearance_nm is not None and clearance_nm < min_clearance_nm:
                    clearance_violations += 1
                    _append_violation(
                        {
                            "rule": "min_clearance",
                            "item_type": type(track).__name__,
                            "id": str(getattr(track, "id", "")),
                            "net": item_net,
                            "layer": item_layer,
                            "clearance_mm": _nm_to_mm(clearance_nm),
                            "required_min_clearance_mm": min_clearance_mm,
                        }
                    )

        if min_clearance_nm is not None:
            for via in board.get_vias():
                item_net = _item_net_name(via)
                if net_name is not None and item_net != net_name:
                    continue

                clearance_nm = None
                for attr in ("clearance", "local_clearance", "own_clearance"):
                    clearance_nm = _numeric_attr(via, attr)
                    if clearance_nm is not None:
                        break
                if clearance_nm is not None and clearance_nm < min_clearance_nm:
                    clearance_violations += 1
                    _append_violation(
                        {
                            "rule": "min_clearance",
                            "item_type": type(via).__name__,
                            "id": str(getattr(via, "id", "")),
                            "net": item_net,
                            "clearance_mm": _nm_to_mm(clearance_nm),
                            "required_min_clearance_mm": min_clearance_mm,
                        }
                    )

        total = width_violations + clearance_violations
        msg_bits = [f"Found {total} violation(s).", f"Width: {width_violations}."]
        if min_clearance_mm is not None:
            msg_bits.append(f"Clearance: {clearance_violations}.")
        msg_bits.append(f"Showing up to {limit} entries.")

        return BoardItemSummary(
            count=total,
            items=violations,
            message=" ".join(msg_bits),
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

    @mcp.tool()
    def board_get_board_bbox(
        include_footprints: bool = True,
        include_tracks: bool = True,
        include_vias: bool = True,
        include_zones: bool = False,
        include_text: bool = False,
    ) -> BoardBBox:
        """
        Compute a best-effort board bounding box in millimeters.

        The result is derived from available geometry points on selected item
        categories and is intended for placement/automation workflows.
        """
        board = _get_board()

        points: list[tuple[float, float]] = []
        if include_footprints:
            for item in board.get_footprints():
                points.extend(_item_points_mm(item))
        if include_tracks:
            for item in board.get_tracks():
                points.extend(_item_points_mm(item))
        if include_vias:
            for item in board.get_vias():
                points.extend(_item_points_mm(item))
        if include_zones:
            for item in board.get_zones():
                points.extend(_item_points_mm(item))
        if include_text:
            try:
                for item in board.get_text():
                    points.extend(_item_points_mm(item))
            except Exception:
                pass

        if not points:
            return BoardBBox(
                found=False,
                sampled_points=0,
                message="No geometry points found for selected categories.",
            )

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        min_x = min(xs)
        max_x = max(xs)
        min_y = min(ys)
        max_y = max(ys)

        return BoardBBox(
            found=True,
            min_x_mm=min_x,
            min_y_mm=min_y,
            max_x_mm=max_x,
            max_y_mm=max_y,
            width_mm=max_x - min_x,
            height_mm=max_y - min_y,
            sampled_points=len(points),
            message="Computed best-effort board bounding box.",
        )

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

    @mcp.tool()
    def board_prepare_fab_outputs(
        output_dir: str,
        gerber_layers: Optional[list[int]] = None,
        include_pdf: bool = True,
        include_position: bool = True,
        drill_format: int = 1,
    ) -> SimpleResult:
        """
        Run a practical fabrication export bundle in one call.

        Outputs:
        - Gerbers (selected layers)
        - NC drill files
        - Optional board PDF
        - Optional pick-and-place position file
        """
        board = _get_board()
        os.makedirs(output_dir, exist_ok=True)

        layers = gerber_layers if gerber_layers else _default_fab_layers(board)
        result_files: dict[str, str] = {}
        step_errors: dict[str, str] = {}

        gerber_dir = os.path.join(output_dir, "gerbers")
        drill_dir = os.path.join(output_dir, "drill")
        pdf_path = os.path.join(output_dir, "board.pdf")
        pos_path = os.path.join(output_dir, "position.pos")

        os.makedirs(gerber_dir, exist_ok=True)
        os.makedirs(drill_dir, exist_ok=True)

        try:
            board.export_gerbers(gerber_dir, layers)
            result_files["gerbers_dir"] = gerber_dir
        except Exception as e:
            step_errors["gerbers"] = str(e)

        try:
            board.export_drill(drill_dir, format=drill_format)
            result_files["drill_dir"] = drill_dir
        except Exception as e:
            step_errors["drill"] = str(e)

        if include_pdf:
            try:
                board.export_pdf(pdf_path)
                result_files["pdf"] = pdf_path
            except Exception as e:
                step_errors["pdf"] = str(e)

        if include_position:
            try:
                board.export_position(pos_path)
                result_files["position"] = pos_path
            except Exception as e:
                step_errors["position"] = str(e)

        success = len(step_errors) == 0
        if success:
            msg = "Fabrication outputs generated."
        else:
            msg = f"Fabrication export completed with {len(step_errors)} error(s)."

        return SimpleResult(
            success=success,
            message=msg,
            data={
                "output_dir": output_dir,
                "layers": layers,
                "files": result_files,
                "errors": step_errors,
            },
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
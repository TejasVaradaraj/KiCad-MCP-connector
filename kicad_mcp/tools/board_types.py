from __future__ import annotations

from typing import Any, Optional, Sequence

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from kicad_mcp.connection import get_kicad
from kicad_mcp.helpers.layers import F_CU
from kicad_mcp.helpers.units import mm_to_nm, nm_to_mm

class ItemDetail(BaseModel):
    success: bool
    item_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    message: str = ""

class MutationResult(BaseModel):
    success: bool
    message: str
    affected_count: int = 0
    details: Optional[dict[str, Any]] = None

class SimpleResult(BaseModel):
    success: bool
    message: str

def _board():
    kicad = get_kicad()
    board = kicad.get_board()
    if board is None:
        raise RuntimeError("No board is currently open in KiCad.")
    return board

def _footprint_reference(fp) -> Optional[str]:
    try:
        field = getattr(fp, "reference_field", None)
        if field is None:
            return None
        text = getattr(field, "text", None)
        if text is None:
            return None
        val = getattr(text, "value", None)
        return str(val) if val is not None else None
    except Exception:
        return None

def _find_footprint_by_ref(board, reference: str):
    for fp in board.get_footprints():
        if _footprint_reference(fp) == reference:
            return fp
    return None

def _summarize_footprint(fp) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        data["id"] = str(getattr(fp, "id", ""))
        ref_field = getattr(fp, "reference_field", None)
        val_field = getattr(fp, "value_field", None)
        if ref_field is not None:
            text = getattr(ref_field, "text", None)
            data["reference"] = getattr(text, "value", None) if text is not None else None
        if val_field is not None:
            text = getattr(val_field, "text", None)
            data["value"] = getattr(text, "value", None) if text is not None else None
        data["layer"] = getattr(fp, "layer", None)
        data["locked"] = getattr(fp, "locked", None)

        pos = getattr(fp, "position", None)
        if pos is not None:
            data["position_mm"] = {
                "x": nm_to_mm(getattr(pos, "x", 0)),
                "y": nm_to_mm(getattr(pos, "y", 0)),
            }

        orient = getattr(fp, "orientation", None)
        if orient is not None:
            # Angle object – best effort
            data["orientation_deg"] = float(orient) if not hasattr(orient, "degrees") else float(orient.degrees())
    except Exception as e:
        data["error"] = str(e)
    return data

def _summarize_track(t) -> dict[str, Any]:
    data: dict[str, Any] = {"type": type(t).__name__}
    try:
        data["id"] = str(getattr(t, "id", ""))
        data["layer"] = getattr(t, "layer", None)
        data["width_mm"] = nm_to_mm(getattr(t, "width", 0))
        data["locked"] = getattr(t, "locked", None)

        start = getattr(t, "start", None)
        end = getattr(t, "end", None)
        if start:
            data["start_mm"] = {"x": nm_to_mm(start.x), "y": nm_to_mm(start.y)}
        if end:
            data["end_mm"] = {"x": nm_to_mm(end.x), "y": nm_to_mm(end.y)}

        net = getattr(t, "net", None)
        if net:
            data["net"] = getattr(net, "name", str(net))

        if hasattr(t, "length"):
            data["length_mm"] = nm_to_mm(t.length())
    except Exception as e:
        data["error"] = str(e)
    return data

def _summarize_via(v) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        data["id"] = str(getattr(v, "id", ""))
        data["diameter_mm"] = nm_to_mm(getattr(v, "diameter", 0)) if hasattr(v, "diameter") else None
        data["drill_mm"] = nm_to_mm(getattr(v, "drill_diameter", 0))
        data["type"] = getattr(v, "type", None)
        data["locked"] = getattr(v, "locked", None)

        pos = getattr(v, "position", None)
        if pos:
            data["position_mm"] = {"x": nm_to_mm(pos.x), "y": nm_to_mm(pos.y)}

        net = getattr(v, "net", None)
        if net:
            data["net"] = getattr(net, "name", str(net))
    except Exception as e:
        data["error"] = str(e)
    return data

def _summarize_zone(z) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        data["id"] = str(getattr(z, "id", ""))
        data["name"] = getattr(z, "name", None)
        data["priority"] = getattr(z, "priority", None)
        data["filled"] = getattr(z, "filled", None)
        data["locked"] = getattr(z, "locked", None)
        data["layers"] = list(getattr(z, "layers", []) or [])

        net = getattr(z, "net", None)
        if net:
            data["net"] = getattr(net, "name", str(net))
    except Exception as e:
        data["error"] = str(e)
    return data

def register_board_types_tools(mcp: MCPServer) -> None:

    @mcp.tool()
    def board_get_footprint(reference: str) -> ItemDetail:
        """
        Get detailed information about a single footprint by its reference
        designator (e.g. "U1", "R12", "C3").

        Returns position (mm), orientation, layer, value, locked state, etc.
        """
        board = _board()
        fp = _find_footprint_by_ref(board, reference)
        if fp is None:
            return ItemDetail(
                success=False,
                item_type="FootprintInstance",
                message=f"Footprint with reference '{reference}' not found.",
            )
        return ItemDetail(
            success=True,
            item_type="FootprintInstance",
            data=_summarize_footprint(fp),
            message=f"Found footprint {reference}.",
        )

    @mcp.tool()
    def board_get_net_info(net_name: str) -> ItemDetail:
        """
        Get information about a net and a summary of items belonging to it.
        """
        board = _board()
        nets = {getattr(n, "name", str(n)): n for n in board.get_nets()}
        if net_name not in nets:
            return ItemDetail(
                success=False,
                item_type="Net",
                message=f"Net '{net_name}' not found.",
            )

        net = nets[net_name]
        items = list(board.get_items_by_net(net))
        summary = {
            "name": net_name,
            "item_count": len(items),
            "sample_items": [
                {"type": type(i).__name__, "repr": str(i)[:120]} for i in items[:15]
            ],
        }
        return ItemDetail(
            success=True,
            item_type="Net",
            data=summary,
            message=f"Net '{net_name}' has {len(items)} connected items.",
        )

    @mcp.tool()
    def board_move_footprint(
        reference: str,
        x_mm: float,
        y_mm: float,
        orientation_deg: Optional[float] = None,
    ) -> MutationResult:
        """
        Move a footprint to a new position (and optionally set orientation).

        Coordinates are in millimeters. The change is performed under a commit
        so it appears as a single undo step in KiCad.
        """
        from kipy.board_types import FootprintInstance  # local import to keep startup light
        from kipy.geometry import Vector2, Angle

        board = _board()
        fp = _find_footprint_by_ref(board, reference)
        if fp is None:
            return MutationResult(
                success=False,
                message=f"Footprint '{reference}' not found.",
            )

        # Mutate the in-memory object
        fp.position = Vector2(mm_to_nm(x_mm), mm_to_nm(y_mm))
        if orientation_deg is not None:
            # Angle construction can vary slightly by version; this is the common pattern
            try:
                fp.orientation = Angle.from_degrees(orientation_deg)
            except Exception:
                fp.orientation = Angle(orientation_deg)  # fallback

        commit = board.begin_commit()
        try:
            board.update_items(fp)
            board.push_commit(commit, f"Move {reference}")
            return MutationResult(
                success=True,
                message=f"Moved {reference} to ({x_mm}, {y_mm}) mm.",
                affected_count=1,
            )
        except Exception as e:
            board.drop_commit(commit)
            return MutationResult(success=False, message=f"Failed to move footprint: {e}")

    @mcp.tool()
    def board_set_footprint_locked(reference: str, locked: bool) -> MutationResult:
        """Lock or unlock a footprint so it cannot be moved accidentally."""
        board = _board()
        fp = _find_footprint_by_ref(board, reference)
        if fp is None:
            return MutationResult(success=False, message=f"Footprint '{reference}' not found.")

        fp.locked = locked
        commit = board.begin_commit()
        try:
            board.update_items(fp)
            board.push_commit(commit, f"{'Lock' if locked else 'Unlock'} {reference}")
            return MutationResult(
                success=True,
                message=f"Footprint {reference} is now {'locked' if locked else 'unlocked'}.",
                affected_count=1,
            )
        except Exception as e:
            board.drop_commit(commit)
            return MutationResult(success=False, message=str(e))

    @mcp.tool()
    def board_create_track(
        start_x_mm: float,
        start_y_mm: float,
        end_x_mm: float,
        end_y_mm: float,
        width_mm: float = 0.25,
        layer: int = F_CU,
        net_name: Optional[str] = None,
    ) -> MutationResult:
        """
        Create a straight copper track between two points.

        All coordinates and width are in millimeters.
        `layer` is the IPC layer ID (F.Cu is 3, not 0). Use board_get_layers
        or layer_from_name to discover values.
        """
        from kipy.board_types import Track
        from kipy.geometry import Vector2

        board = _board()

        track = Track()
        track.start = Vector2(mm_to_nm(start_x_mm), mm_to_nm(start_y_mm))
        track.end = Vector2(mm_to_nm(end_x_mm), mm_to_nm(end_y_mm))
        track.width = mm_to_nm(width_mm)
        track.layer = layer

        if net_name:
            nets = {getattr(n, "name", str(n)): n for n in board.get_nets()}
            if net_name in nets:
                track.net = nets[net_name]

        commit = board.begin_commit()
        try:
            created = board.create_items(track)
            board.push_commit(commit, "Create track")
            return MutationResult(
                success=True,
                message="Track created.",
                affected_count=len(created) if created else 1,
                details={"created": str(created)[:300]},
            )
        except Exception as e:
            board.drop_commit(commit)
            return MutationResult(success=False, message=f"Failed to create track: {e}")

    @mcp.tool()
    def board_create_via(
        x_mm: float,
        y_mm: float,
        drill_mm: float = 0.3,
        diameter_mm: float = 0.6,
        net_name: Optional[str] = None,
    ) -> MutationResult:
        """
        Create a through-hole via at the given position (millimeters).
        """
        from kipy.board_types import Via
        from kipy.geometry import Vector2

        board = _board()

        via = Via()
        via.position = Vector2(mm_to_nm(x_mm), mm_to_nm(y_mm))
        via.drill_diameter = mm_to_nm(drill_mm)
        # diameter property may set padstack mode automatically
        try:
            via.diameter = mm_to_nm(diameter_mm)
        except Exception:
            pass

        if net_name:
            nets = {getattr(n, "name", str(n)): n for n in board.get_nets()}
            if net_name in nets:
                via.net = nets[net_name]

        commit = board.begin_commit()
        try:
            created = board.create_items(via)
            board.push_commit(commit, "Create via")
            return MutationResult(
                success=True,
                message="Via created.",
                affected_count=len(created) if created else 1,
            )
        except Exception as e:
            board.drop_commit(commit)
            return MutationResult(success=False, message=f"Failed to create via: {e}")

    @mcp.tool()
    def board_remove_items_by_id(item_ids: list[str]) -> MutationResult:
        """
        Remove one or more board items by their KIID (unique ID strings).

        You can obtain IDs from the various list/get tools.
        """
        board = _board()
        # The API expects KIID objects or similar; we pass strings and let the
        # binding coerce where possible. Adjust once exact KIID construction is confirmed.
        commit = board.begin_commit()
        try:
            board.remove_items_by_id(item_ids)
            board.push_commit(commit, f"Remove {len(item_ids)} item(s)")
            return MutationResult(
                success=True,
                message=f"Requested removal of {len(item_ids)} item(s).",
                affected_count=len(item_ids),
            )
        except Exception as e:
            board.drop_commit(commit)
            return MutationResult(success=False, message=f"Remove failed: {e}")

    @mcp.tool()
    def board_list_zones_detailed(limit: int = 20) -> list[dict[str, Any]]:
        """
        Return a more detailed summary of zones (name, net, layers, priority, filled).
        """
        board = _board()
        zones = list(board.get_zones())[:limit]
        return [_summarize_zone(z) for z in zones]

    @mcp.tool()
    def board_get_zone(name: str) -> ItemDetail:
        """Get detailed information about a zone by its name."""
        board = _board()
        for z in board.get_zones():
            if getattr(z, "name", None) == name:
                return ItemDetail(
                    success=True,
                    item_type="Zone",
                    data=_summarize_zone(z),
                    message=f"Found zone '{name}'.",
                )
        return ItemDetail(
            success=False,
            item_type="Zone",
            message=f"Zone '{name}' not found.",
        )

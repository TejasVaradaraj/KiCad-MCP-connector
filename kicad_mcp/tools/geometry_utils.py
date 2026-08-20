from __future__ import annotations

from typing import Any, Optional

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from kicad_mcp.helpers.units import mm_to_nm, nm_to_mm, mils_to_nm, nm_to_mils

class PointMM(BaseModel):
    x: float
    y: float

class UnitConversionResult(BaseModel):
    value: float
    unit: str
    message: str = ""

class LayerInfo(BaseModel):
    layer_id: int
    canonical_name: str
    is_copper: bool
    message: str = ""

class ArcCalcResult(BaseModel):
    success: bool
    radius_mm: Optional[float] = None
    center_mm: Optional[PointMM] = None
    start_angle_deg: Optional[float] = None
    end_angle_deg: Optional[float] = None
    angle_deg: Optional[float] = None
    message: str = ""

class DistanceResult(BaseModel):
    distance_mm: float
    dx_mm: float
    dy_mm: float

class AngleResult(BaseModel):
    degrees: float
    radians: float
    normalized_0_360: float
    normalized_pm180: float

def register_geometry_utils_tools(mcp: MCPServer) -> None:

    @mcp.tool()
    def convert_mm_to_nm(value_mm: float) -> UnitConversionResult:
        """
        Convert millimeters to nanometers (the unit used by the KiCad IPC API).
        """
        nm = mm_to_nm(value_mm)
        return UnitConversionResult(value=float(nm), unit="nm",
                                    message=f"{value_mm} mm = {nm} nm")

    @mcp.tool()
    def convert_nm_to_mm(value_nm: float) -> UnitConversionResult:
        """
        Convert nanometers (KiCad IPC units) to millimeters.
        """
        mm = nm_to_mm(value_nm)
        return UnitConversionResult(value=mm, unit="mm",
                                    message=f"{value_nm} nm = {mm} mm")

    @mcp.tool()
    def convert_mils_to_nm(value_mils: float) -> UnitConversionResult:
        """Convert mils (thousandths of an inch) to nanometers."""
        nm = mils_to_nm(value_mils)
        return UnitConversionResult(value=float(nm), unit="nm",
                                    message=f"{value_mils} mils = {nm} nm")

    @mcp.tool()
    def convert_nm_to_mils(value_nm: float) -> UnitConversionResult:
        """Convert nanometers to mils."""
        mils = nm_to_mils(value_nm)
        return UnitConversionResult(value=mils, unit="mils",
                                    message=f"{value_nm} nm = {mils} mils")

    @mcp.tool()
    def layer_get_canonical_name(layer_id: int) -> LayerInfo:
        """
        Return the canonical name of a board layer ID
        (e.g. 3 → "F.Cu", 34 → "B.Cu"). Layer 0 is BL_UNKNOWN, not F.Cu.
        """
        from kipy.util.board_layer import canonical_name, is_copper_layer

        try:
            name = canonical_name(layer_id)
            copper = is_copper_layer(layer_id)
            return LayerInfo(
                layer_id=layer_id,
                canonical_name=name,
                is_copper=copper,
                message=f"Layer {layer_id} is '{name}'",
            )
        except Exception as e:
            return LayerInfo(
                layer_id=layer_id,
                canonical_name="",
                is_copper=False,
                message=f"Error: {e}",
            )

    @mcp.tool()
    def layer_from_name(name: str) -> LayerInfo:
        """
        Convert a canonical layer name (e.g. "F.Cu", "B.SilkS", "Edge.Cuts")
        into its integer layer ID.
        """
        from kipy.util.board_layer import layer_from_canonical_name, is_copper_layer, canonical_name

        try:
            layer_id = layer_from_canonical_name(name)
            # BL_UNKNOWN is usually a specific value; treat negative / special as failure
            copper = is_copper_layer(layer_id)
            resolved = canonical_name(layer_id)
            return LayerInfo(
                layer_id=layer_id,
                canonical_name=resolved,
                is_copper=copper,
                message=f"'{name}' → layer ID {layer_id}",
            )
        except Exception as e:
            return LayerInfo(
                layer_id=-1,
                canonical_name=name,
                is_copper=False,
                message=f"Error resolving layer name: {e}",
            )

    @mcp.tool()
    def layer_list_copper() -> list[dict[str, Any]]:
        """
        List all copper layers with their IDs and canonical names.
        Useful when deciding which layer to draw tracks on.
        """
        from kipy.util.board_layer import iter_copper_layers, canonical_name

        result = []
        for layer_id in iter_copper_layers():
            try:
                result.append({
                    "layer_id": layer_id,
                    "canonical_name": canonical_name(layer_id),
                })
            except Exception:
                result.append({"layer_id": layer_id, "canonical_name": str(layer_id)})
        return result

    @mcp.tool()
    def geometry_distance(
        x1_mm: float, y1_mm: float,
        x2_mm: float, y2_mm: float,
    ) -> DistanceResult:
        """
        Calculate the Euclidean distance between two points (millimeters).
        Also returns the dx/dy components.
        """
        dx = x2_mm - x1_mm
        dy = y2_mm - y1_mm
        dist = (dx * dx + dy * dy) ** 0.5
        return DistanceResult(distance_mm=dist, dx_mm=dx, dy_mm=dy)

    @mcp.tool()
    def geometry_normalize_angle(degrees: float) -> AngleResult:
        """
        Normalize an angle in degrees to both [0, 360) and [-180, 180) ranges.
        Also returns the radian equivalent.
        """
        import math
        from kipy.geometry import normalize_angle_degrees, normalize_angle_pi_radians

        norm_360 = normalize_angle_degrees(degrees)
        # normalize_angle_pi_radians expects radians
        rad = math.radians(degrees)
        norm_pm_pi = normalize_angle_pi_radians(rad)
        norm_pm180 = math.degrees(norm_pm_pi)

        return AngleResult(
            degrees=degrees,
            radians=rad,
            normalized_0_360=norm_360,
            normalized_pm180=norm_pm180,
        )

    @mcp.tool()
    def geometry_arc_properties(
        start_x_mm: float, start_y_mm: float,
        mid_x_mm: float, mid_y_mm: float,
        end_x_mm: float, end_y_mm: float,
    ) -> ArcCalcResult:
        """
        Given an arc defined by start, mid, and end points (mm),
        compute radius, center, start/end angles, and sweep angle.

        Useful when inspecting or recreating ArcTracks / board arcs.
        """
        from kipy.geometry import (
            Vector2,
            arc_radius, arc_center,
            arc_start_angle_degrees, arc_end_angle_degrees, arc_angle,
        )
        import math

        try:
            start = Vector2.from_xy_mm(start_x_mm, start_y_mm)
            mid = Vector2.from_xy_mm(mid_x_mm, mid_y_mm)
            end = Vector2.from_xy_mm(end_x_mm, end_y_mm)

            radius_nm = arc_radius(start, mid, end)
            center = arc_center(start, mid, end)
            start_ang = arc_start_angle_degrees(start, mid, end)
            end_ang = arc_end_angle_degrees(start, mid, end)
            sweep_rad = arc_angle(start, mid, end)

            center_mm = None
            if center is not None:
                center_mm = PointMM(x=nm_to_mm(center.x), y=nm_to_mm(center.y))

            return ArcCalcResult(
                success=True,
                radius_mm=nm_to_mm(radius_nm) if radius_nm else 0.0,
                center_mm=center_mm,
                start_angle_deg=start_ang,
                end_angle_deg=end_ang,
                angle_deg=math.degrees(sweep_rad) if sweep_rad is not None else None,
                message="Arc properties calculated.",
            )
        except Exception as e:
            return ArcCalcResult(success=False, message=f"Failed to calculate arc: {e}")

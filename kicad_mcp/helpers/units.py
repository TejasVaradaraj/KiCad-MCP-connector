# kicad_mcp/helpers/units.py
"""Unit conversion helpers. KiCad IPC API uses nanometers everywhere."""

NM_PER_MM = 1_000_000
NM_PER_MIL = 25_400  # 1 mil = 0.0254 mm = 25400 nm


def mm_to_nm(mm: float) -> int:
    return int(round(mm * NM_PER_MM))


def nm_to_mm(nm: int | float) -> float:
    return float(nm) / NM_PER_MM


def mils_to_nm(mils: float) -> int:
    return int(round(mils * NM_PER_MIL))


def nm_to_mils(nm: int | float) -> float:
    return float(nm) / NM_PER_MIL

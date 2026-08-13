# kicad_mcp/connection.py
from functools import lru_cache

@lru_cache(maxsize=1)
def get_kicad():
    """Lazy connection to KiCad. Real implementation comes in the KiCad tools commit."""
    raise NotImplementedError("Connection not implemented yet")
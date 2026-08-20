from __future__ import annotations

from mcp.server import MCPServer

from kicad_mcp.tools.board import register_board_tools
from kicad_mcp.tools.board_types import register_board_types_tools
from kicad_mcp.tools.common_types import register_common_types_tools
from kicad_mcp.tools.geometry_utils import register_geometry_utils_tools
from kicad_mcp.tools.kicad_core import register_kicad_core_tools
from kicad_mcp.tools.project import register_project_tools


def create_server(**kwargs) -> MCPServer:
    mcp = MCPServer("kicad-mcp", **kwargs)
    register_kicad_core_tools(mcp)
    register_board_tools(mcp)
    register_board_types_tools(mcp)
    register_geometry_utils_tools(mcp)
    register_common_types_tools(mcp)
    register_project_tools(mcp)
    return mcp


mcp = create_server()


def run() -> None:
    mcp.run(transport="stdio")

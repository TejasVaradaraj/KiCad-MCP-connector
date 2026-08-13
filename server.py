# server.py
from mcp.server import MCPServer
from kicad_mcp.tools.kicad_core import register_kicad_core_tools
from kicad_mcp.tools.board import register_board_tools
from kicad_mcp.tools.board_types import register_board_types_tools
from kicad_mcp.tools.geometry_utils import register_geometry_utils_tools
from kicad_mcp.tools.common_types import register_common_types_tools
from kicad_mcp.tools.project import register_project_tools

mcp = MCPServer("kicad-mcp")

register_kicad_core_tools(mcp)
register_board_tools(mcp)
register_board_types_tools(mcp)
register_geometry_utils_tools(mcp)
register_common_types_tools(mcp)
register_project_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="stdio")
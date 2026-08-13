# server.py
from mcp.server import MCPServer
from kicad_mcp.tools.kicad_core import register_kicad_core_tools

mcp = MCPServer("kicad-mcp")
register_kicad_core_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="stdio")
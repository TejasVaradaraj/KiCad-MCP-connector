# server.py
from mcp.server import MCPServer

mcp = MCPServer("kicad-mcp")

@mcp.tool()
def ping() -> str:
    """Health check tool while the real tools are being added."""
    return "kicad-mcp is alive"

if __name__ == "__main__":
    mcp.run(transport="stdio")
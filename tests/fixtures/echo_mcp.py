"""Tiny MCP server used by the tests (stands in for Unity/Photoshop/Blender servers)."""
from mcp.server.mcpserver import MCPServer

server = MCPServer(name="echo")


@server.tool()
def echo(text: str) -> str:
    """Echo text back."""
    return f"echo: {text}"


@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


if __name__ == "__main__":
    server.run()

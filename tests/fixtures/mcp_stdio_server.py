import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

server = FastMCP("KCode test server")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def environment(name: str) -> str:
    """Return one environment variable for integration testing."""
    return os.environ.get(name, "<missing>")


if __name__ == "__main__":
    server.run(transport="stdio")

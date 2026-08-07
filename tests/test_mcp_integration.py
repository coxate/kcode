import asyncio
import socket
import sys
from pathlib import Path

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from kcode.config import HttpMcpServerConfig, StdioMcpServerConfig
from kcode.mcp.manager import McpManager
from kcode.mcp.tool import McpToolArguments
from kcode.mcp.trust import McpTrustStore
from kcode.tools.base import ToolContext, ToolEffect


async def allow(_request):
    return True


async def test_stdio_initialize_list_call_and_minimal_environment(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    config = StdioMcpServerConfig(
        name="local",
        source="user",
        type="stdio",
        command=sys.executable,
        args=(str(fixture),),
        env={"EXPLICIT_VALUE": "visible"},
    )
    manager = McpManager(
        (config,),
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ={
            "PATH": "/usr/bin:/bin",
            "EXPLICIT_VALUE": "visible",
            "UNDECLARED_SECRET": "must-not-leak",
        },
        connect_timeout=10,
    )
    await manager.prepare(allow)
    summary = await manager.connect_all()
    try:
        assert summary.failed_servers == ()
        assert summary.connected_servers == ("local",)
        assert [tool.spec.name for tool in summary.tools] == ["mcp__local__environment"]
        wrapped = summary.tools[0]
        assert wrapped.spec.effect == ToolEffect.READ_ONLY
        explicit = await wrapped.execute(
            McpToolArguments(name="EXPLICIT_VALUE"),
            ToolContext(tmp_path),
        )
        hidden = await wrapped.execute(
            McpToolArguments(name="UNDECLARED_SECRET"),
            ToolContext(tmp_path),
        )
        assert explicit.data == {"content": "visible"}
        assert hidden.data == {"content": "<missing>"}
    finally:
        await manager.close()


@pytest.mark.parametrize("json_response", [True, False])
async def test_streamable_http_json_and_sse_with_explicit_headers(
    tmp_path: Path,
    json_response: bool,
) -> None:
    remote = FastMCP(
        "KCode HTTP test",
        stateless_http=True,
        json_response=json_response,
    )

    @remote.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def echo(value: str) -> str:
        return value

    captured_headers = []
    remote_app = remote.streamable_http_app()

    async def recording_app(scope, receive, send):
        if scope["type"] == "http":
            captured_headers.append(dict(scope["headers"]))
        await remote_app(scope, receive, send)

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", 0))
    except PermissionError:
        listener.close()
        pytest.skip("sandbox does not allow a loopback test server")
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(recording_app, log_level="error", lifespan="on"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0.01)

    config = HttpMcpServerConfig(
        name="remote",
        source="user",
        type="http",
        url=f"http://127.0.0.1:{port}/mcp",
        headers={"X-KCode-Test": "expected"},
    )
    manager = McpManager(
        (config,),
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ={},
        connect_timeout=10,
    )
    try:
        await manager.prepare(allow)
        summary = await manager.connect_all()
        assert summary.failed_servers == ()
        result = await summary.tools[0].execute(
            McpToolArguments(value="hello"),
            ToolContext(tmp_path),
        )
        assert result.data == {"content": "hello"}
        assert any(headers.get(b"x-kcode-test") == b"expected" for headers in captured_headers)
    finally:
        await manager.close()
        server.should_exit = True
        await asyncio.wait_for(server_task, 5)

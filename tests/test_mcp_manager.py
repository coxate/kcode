import asyncio
from pathlib import Path

from mcp import types

from kcode.config import HttpMcpServerConfig, StdioMcpServerConfig
from kcode.mcp.manager import McpManager, minimal_stdio_environment
from kcode.mcp.trust import McpTrustStore
from kcode.tools.base import ToolEffect


def stdio(source="project", env=None) -> StdioMcpServerConfig:
    return StdioMcpServerConfig(
        name="demo",
        source=source,
        type="stdio",
        command="python",
        env=env or {},
    )


class FakeManager(McpManager):
    def __init__(self, *args, remote_tools=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.remote_tools = list(remote_tools)
        self.owner_tasks = []

    async def _owner(self, record):
        self.owner_tasks.append(asyncio.current_task())
        try:
            record.ready.set_result(self.remote_tools)
            await record.stop.wait()
        finally:
            record.handle.closing = True


async def accept(_request):
    return True


async def reject(_request):
    return False


async def test_rejected_project_server_has_no_environment_read(tmp_path: Path) -> None:
    class GuardedEnvironment(dict):
        def __getitem__(self, key):
            raise AssertionError("environment value was read before trust")

    manager = McpManager(
        (stdio(env={"TOKEN": "${SECRET}"}),),
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ=GuardedEnvironment(SECRET="hidden"),
    )
    assert await manager.prepare(reject) == ()
    summary = await manager.connect_all()
    assert summary.skipped_servers == ("demo",)
    assert summary.connected_servers == ()


async def test_prepare_expands_after_trust_and_minimizes_stdio_env(tmp_path: Path) -> None:
    manager = McpManager(
        (stdio(env={"TOKEN": "Bearer ${SECRET}"}),),
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ={"PATH": "/bin", "SECRET": "hidden", "UNDECLARED": "leak"},
    )
    prepared = await manager.prepare(accept)
    config = prepared[0].config
    assert isinstance(config, StdioMcpServerConfig)
    assert config.env == {"PATH": "/bin", "TOKEN": "Bearer hidden"}
    assert prepared[0].sensitive_values == ("hidden", "Bearer hidden")


async def test_missing_environment_skips_only_affected_server(tmp_path: Path) -> None:
    servers = (
        stdio(source="user", env={"TOKEN": "${MISSING}"}),
        HttpMcpServerConfig(
            name="working",
            source="user",
            type="http",
            url="https://example.test/mcp",
        ),
    )
    manager = FakeManager(
        servers,
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ={},
    )
    await manager.prepare(accept)
    summary = await manager.connect_all()
    assert summary.skipped_servers == ("demo",)
    assert summary.connected_servers == ("working",)
    await manager.close()


async def test_connect_discovers_tools_and_close_reaps_owners(tmp_path: Path) -> None:
    remote_tools = [
        types.Tool(
            name="read",
            description="Read data",
            inputSchema={"type": "object"},
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
        types.Tool(name="write", inputSchema={"type": "object"}),
        types.Tool(name="bad.name", inputSchema={"type": "object"}),
        types.Tool(name="read", inputSchema={"type": "object"}),
    ]
    manager = FakeManager(
        (stdio(source="user"),),
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ={},
        remote_tools=remote_tools,
    )
    await manager.prepare(accept)
    summary = await manager.connect_all()
    assert summary.connected_servers == ("demo",)
    assert [tool.spec.name for tool in summary.tools] == [
        "mcp__demo__read",
        "mcp__demo__write",
    ]
    assert summary.tools[0].spec.effect == ToolEffect.READ_ONLY
    assert summary.tools[1].spec.effect == ToolEffect.SIDE_EFFECT
    assert len(summary.warnings) == 2
    tasks = tuple(manager.owner_tasks)
    await manager.close()
    assert all(task.done() for task in tasks)


async def test_startup_timeout_is_isolated(tmp_path: Path) -> None:
    class SlowManager(McpManager):
        async def _owner(self, record):
            await asyncio.sleep(10)

    manager = SlowManager(
        (stdio(source="user"),),
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ={},
        connect_timeout=0.01,
    )
    await manager.prepare(accept)
    summary = await manager.connect_all()
    assert summary.failed_servers == ("demo",)
    assert "timed out" in summary.warnings[0]
    await manager.close()


async def test_servers_start_concurrently(tmp_path: Path) -> None:
    class DelayedManager(McpManager):
        async def _owner(self, record):
            await asyncio.sleep(0.05)
            record.ready.set_result([])
            await record.stop.wait()

    first = stdio(source="user")
    second = first.model_copy(update={"name": "second"})
    manager = DelayedManager(
        (first, second),
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ={},
    )
    await manager.prepare(accept)
    started = asyncio.get_running_loop().time()
    summary = await manager.connect_all()
    elapsed = asyncio.get_running_loop().time() - started
    assert summary.connected_servers == ("demo", "second")
    assert elapsed < 0.09
    await manager.close()


async def test_close_timeout_cancels_stubborn_owner(tmp_path: Path) -> None:
    class StubbornManager(McpManager):
        async def _owner(self, record):
            record.ready.set_result([])
            await asyncio.Event().wait()

    manager = StubbornManager(
        (stdio(source="user"),),
        tmp_path,
        McpTrustStore(tmp_path / "trust.json"),
        environ={},
        close_timeout=0.01,
    )
    await manager.prepare(accept)
    await manager.connect_all()
    task = manager._records[0].task
    await manager.close()
    assert task is not None and task.done()


def test_minimal_environment_drops_unlisted_secrets() -> None:
    assert minimal_stdio_environment({"PATH": "/bin", "HOME": "/home/user", "TOKEN": "secret"}) == {
        "PATH": "/bin",
        "HOME": "/home/user",
    }

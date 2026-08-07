from __future__ import annotations

import asyncio
import os
import re
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from kcode.config import (
    HttpMcpServerConfig,
    McpServerConfig,
    MissingMcpEnvironment,
    StdioMcpServerConfig,
    expand_mcp_server,
    mcp_environment_variables,
)
from kcode.mcp.tool import McpTool, effect_from_annotations
from kcode.mcp.trust import McpTrustRequest, McpTrustStore, trust_fingerprint
from kcode.tools.base import Tool

MCP_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
BASIC_ENVIRONMENT = {
    "PATH",
    "HOME",
    "USERPROFILE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SYSTEMROOT",
    "WINDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
}

TrustHandler = Callable[[McpTrustRequest], Awaitable[bool]]


def minimal_stdio_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in environ.items() if key in BASIC_ENVIRONMENT}


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


@dataclass(frozen=True, slots=True)
class PreparedMcpServer:
    config: McpServerConfig
    sensitive_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class McpStartupSummary:
    tools: tuple[Tool, ...]
    connected_servers: tuple[str, ...]
    skipped_servers: tuple[str, ...]
    failed_servers: tuple[str, ...]
    warnings: tuple[str, ...]
    sensitive_values: tuple[str, ...]

    @property
    def message(self) -> str:
        return (
            f"MCP: {len(self.connected_servers)} server(s) connected, "
            f"{len(self.tools)} tool(s) registered, "
            f"{len(self.skipped_servers)} skipped, {len(self.failed_servers)} failed."
        )


class McpServerHandle:
    def __init__(self, server_name: str, call_timeout: float) -> None:
        self.server_name = server_name
        self.call_timeout = call_timeout
        self.session: ClientSession | None = None
        self.closing = False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        session = self.session
        if session is None or self.closing:
            raise RuntimeError(f"MCP server {self.server_name} is not connected")
        return await asyncio.wait_for(
            session.call_tool(name, arguments=arguments),
            timeout=self.call_timeout,
        )


@dataclass(slots=True)
class _OwnerRecord:
    config: McpServerConfig
    secrets: tuple[str, ...]
    handle: McpServerHandle
    ready: asyncio.Future[list[types.Tool]]
    stop: asyncio.Event
    task: asyncio.Task[None] | None = None


class McpManager:
    def __init__(
        self,
        servers: tuple[McpServerConfig, ...],
        project_root: Path,
        trust_store: McpTrustStore,
        *,
        environ: Mapping[str, str] | None = None,
        connect_timeout: float = 30,
        call_timeout: float = 30,
        close_timeout: float = 5,
    ) -> None:
        self.servers = servers
        self.project_root = project_root.resolve()
        self.trust_store = trust_store
        self.environ = os.environ if environ is None else environ
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.close_timeout = close_timeout
        self._records: list[_OwnerRecord] = []
        self._prepared: tuple[PreparedMcpServer, ...] = ()
        self._skipped: list[str] = []
        self._warnings: list[str] = []
        self._closing = False

    def _trust_request(self, server: McpServerConfig) -> McpTrustRequest:
        if isinstance(server, StdioMcpServerConfig):
            target = " ".join((server.command, *server.args))
        else:
            target = server.url
        return McpTrustRequest(
            self.project_root,
            server.name,
            server.type,
            target,
            mcp_environment_variables(server),
            trust_fingerprint(self.project_root, server),
        )

    async def prepare(self, trust: TrustHandler) -> tuple[PreparedMcpServer, ...]:
        prepared: list[PreparedMcpServer] = []
        self._skipped.clear()
        self._warnings.clear()
        for server in self.servers:
            if server.source == "project":
                request = self._trust_request(server)
                try:
                    already_trusted = self.trust_store.is_trusted(
                        self.project_root, request.fingerprint
                    )
                except OSError:
                    self._skipped.append(server.name)
                    self._warnings.append(
                        f"KCode skipped MCP server {server.name!r}: trust settings "
                        "could not be read safely."
                    )
                    continue
                if not already_trusted:
                    if not await trust(request):
                        self._skipped.append(server.name)
                        self._warnings.append(
                            f"KCode skipped untrusted MCP server {server.name!r}."
                        )
                        continue
                    try:
                        self.trust_store.trust(self.project_root, request.fingerprint)
                    except OSError:
                        self._skipped.append(server.name)
                        self._warnings.append(
                            f"KCode skipped MCP server {server.name!r}: trust could "
                            "not be saved safely."
                        )
                        continue
            try:
                resolved, secrets = expand_mcp_server(server, self.environ)
            except MissingMcpEnvironment as exc:
                self._skipped.append(server.name)
                variables = ", ".join(exc.variables)
                self._warnings.append(
                    f"KCode skipped MCP server {server.name!r}: missing environment "
                    f"variable(s) {variables}."
                )
                continue
            if isinstance(resolved, StdioMcpServerConfig):
                child_env = minimal_stdio_environment(self.environ)
                child_env.update(resolved.env)
                resolved = resolved.model_copy(update={"env": child_env})
            prepared.append(PreparedMcpServer(resolved, secrets))
        self._warnings.extend(self.trust_store.warnings)
        self._prepared = tuple(prepared)
        return self._prepared

    async def _run_session_owner(
        self,
        record: _OwnerRecord,
        read_stream: Any,
        write_stream: Any,
    ) -> None:
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=self.call_timeout),
        ) as session:
            record.handle.session = session
            await session.initialize()
            response = await session.list_tools()
            if not record.ready.done():
                record.ready.set_result(response.tools)
            await record.stop.wait()

    async def _owner(self, record: _OwnerRecord) -> None:
        try:
            config = record.config
            if isinstance(config, StdioMcpServerConfig):
                parameters = StdioServerParameters(
                    command=config.command,
                    args=list(config.args),
                    env=config.env,
                )
                async with stdio_client(parameters, errlog=subprocess.DEVNULL) as (
                    read,
                    write,
                ):
                    await self._run_session_owner(record, read, write)
            elif isinstance(config, HttpMcpServerConfig):
                async with httpx.AsyncClient(
                    headers=config.headers,
                    trust_env=False,
                ) as client:
                    async with streamable_http_client(
                        config.url,
                        http_client=client,
                    ) as (read, write, _):
                        await self._run_session_owner(record, read, write)
        except asyncio.CancelledError:
            if not record.ready.done():
                record.ready.cancel()
            raise
        except Exception as exc:
            if not record.ready.done():
                record.ready.set_exception(exc)
        finally:
            record.handle.closing = True
            record.handle.session = None

    def _new_record(self, prepared: PreparedMcpServer) -> _OwnerRecord:
        loop = asyncio.get_running_loop()
        handle = McpServerHandle(prepared.config.name, self.call_timeout)
        ready: asyncio.Future[list[types.Tool]] = loop.create_future()
        stop = asyncio.Event()
        record = _OwnerRecord(
            prepared.config,
            prepared.sensitive_values,
            handle,
            ready,
            stop,
        )
        record.task = asyncio.create_task(
            self._owner(record),
            name=f"kcode-mcp-{prepared.config.name}",
        )
        return record

    async def _wait_ready(
        self,
        record: _OwnerRecord,
    ) -> tuple[_OwnerRecord, list[types.Tool] | None, str | None]:
        try:
            tools = await asyncio.wait_for(
                asyncio.shield(record.ready),
                timeout=self.connect_timeout,
            )
            return record, tools, None
        except asyncio.TimeoutError:
            error = "startup timed out"
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
        record.stop.set()
        assert record.task is not None
        record.task.cancel()
        await asyncio.gather(record.task, return_exceptions=True)
        return record, None, _redact(error, record.secrets)

    async def connect_all(self) -> McpStartupSummary:
        if self._records:
            raise RuntimeError("MCP manager has already started")
        self._records = [self._new_record(server) for server in self._prepared]
        results = await asyncio.gather(*(self._wait_ready(record) for record in self._records))
        connected: list[str] = []
        failed: list[str] = []
        tools: list[Tool] = []
        warnings = list(self._warnings)

        for record, remote_tools, error in results:
            if remote_tools is None:
                failed.append(record.config.name)
                warnings.append(
                    f"KCode could not start MCP server {record.config.name!r}: {error}."
                )
                continue
            connected.append(record.config.name)
            seen: set[str] = set()
            for remote in remote_tools:
                full_name = f"mcp__{record.config.name}__{remote.name}"
                if not MCP_TOOL_NAME.fullmatch(full_name):
                    warnings.append(f"KCode skipped MCP tool {full_name!r}: invalid tool name.")
                    continue
                if remote.name in seen:
                    warnings.append(f"KCode skipped duplicate MCP tool {full_name!r}.")
                    continue
                seen.add(remote.name)
                tools.append(
                    McpTool(
                        name=full_name,
                        remote_name=remote.name,
                        description=remote.description
                        or f"Tool {remote.name} from MCP server {record.config.name}.",
                        parameters=remote.inputSchema,
                        effect=effect_from_annotations(remote),
                        handle=record.handle,
                        server_name=record.config.name,
                    )
                )
        active_records = {name for name in connected}
        self._records = [record for record in self._records if record.config.name in active_records]
        sensitive = tuple(
            dict.fromkeys(
                value for prepared in self._prepared for value in prepared.sensitive_values if value
            )
        )
        return McpStartupSummary(
            tuple(tools),
            tuple(connected),
            tuple(self._skipped),
            tuple(failed),
            tuple(warnings),
            sensitive,
        )

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        for record in self._records:
            record.handle.closing = True
            record.stop.set()
        tasks = [
            record.task
            for record in self._records
            if record.task is not None and not record.task.done()
        ]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self.close_timeout,
                )
            except asyncio.TimeoutError:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        self._records.clear()

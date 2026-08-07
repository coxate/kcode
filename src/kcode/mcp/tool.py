from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Protocol

from mcp import types
from pydantic import ConfigDict

from kcode.tools.base import (
    ToolArguments,
    ToolContext,
    ToolEffect,
    ToolResult,
    ToolSpec,
)


class McpToolArguments(ToolArguments):
    model_config = ConfigDict(extra="allow")


class McpCallHandle(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult: ...


class McpTool:
    def __init__(
        self,
        *,
        name: str,
        remote_name: str,
        description: str,
        parameters: dict[str, Any],
        effect: ToolEffect,
        handle: McpCallHandle,
        server_name: str,
    ) -> None:
        self.remote_name = remote_name
        self.handle = handle
        self.server_name = server_name
        self._warned_content_types: set[str] = set()
        self._spec = ToolSpec(
            name,
            description,
            McpToolArguments,
            effect,
            parameters,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        try:
            result = await self.handle.call_tool(
                self.remote_name,
                arguments.model_dump(mode="json"),
            )
        except asyncio.TimeoutError:
            return ToolResult.failure(
                "timeout",
                f"MCP tool {self.spec.name} exceeded its timeout.",
                status="timeout",
                details={"tool": self.spec.name, "server": self.server_name},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ToolResult.failure(
                "mcp_transport_error",
                f"MCP server {self.server_name} could not complete the tool call: "
                f"{exc.__class__.__name__}: {exc}",
                details={"tool": self.spec.name, "server": self.server_name},
            )

        texts: list[str] = []
        warnings: list[str] = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                texts.append(block.text)
                continue
            content_type = getattr(block, "type", block.__class__.__name__)
            if content_type not in self._warned_content_types:
                self._warned_content_types.add(content_type)
                warnings.append(
                    f"KCode ignored unsupported MCP content type {content_type!r} "
                    f"from {self.spec.name}."
                )
        content = "".join(texts)
        if result.isError:
            failure = ToolResult.failure(
                "mcp_tool_error",
                content or f"MCP tool {self.spec.name} reported an error.",
                details={"tool": self.spec.name, "server": self.server_name},
            )
            return replace(failure, warnings=tuple(warnings))
        return ToolResult.success({"content": content}, warnings=tuple(warnings))


def effect_from_annotations(tool: types.Tool) -> ToolEffect:
    annotations = tool.annotations
    return (
        ToolEffect.READ_ONLY
        if annotations is not None and annotations.readOnlyHint is True
        else ToolEffect.SIDE_EFFECT
    )

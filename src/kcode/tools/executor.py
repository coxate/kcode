from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from kcode.tools.base import (
    ApprovalHandler,
    JSONValue,
    RunCommandArgs,
    ToolCall,
    ToolContext,
    ToolError,
    ToolExecutionError,
    ToolResult,
)
from kcode.tools.policy import ToolPolicy
from kcode.tools.registry import ToolRegistry


def _redact_value(value: JSONValue, secrets: tuple[str, ...]) -> JSONValue:
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item, secrets) for key, item in value.items()}
    return value


def redact_result(result: ToolResult, secrets: tuple[str, ...]) -> ToolResult:
    data = _redact_value(dict(result.data), secrets) if result.data is not None else None
    error = result.error
    if error is not None:
        error = ToolError(
            error.code,
            str(_redact_value(error.message, secrets)),
            _redact_value(dict(error.details), secrets),  # type: ignore[arg-type]
        )
    warnings = tuple(str(_redact_value(item, secrets)) for item in result.warnings)
    return replace(result, data=data, error=error, warnings=warnings)  # type: ignore[arg-type]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, policy: ToolPolicy) -> None:
        self.registry = registry
        self.policy = policy

    async def execute(
        self,
        call: ToolCall,
        context: ToolContext,
        approve: ApprovalHandler,
    ) -> ToolResult:
        started = time.monotonic()
        tool = self.registry.get(call.name)
        if tool is None:
            return ToolResult.failure("unknown_tool", f"Unknown tool: {call.name}", details={"tool": call.name})
        try:
            raw = json.loads(call.arguments_json)
            if not isinstance(raw, dict):
                raise ValueError("Arguments must be a JSON object.")
        except (json.JSONDecodeError, ValueError) as exc:
            return ToolResult.failure("invalid_arguments", f"Invalid tool arguments JSON: {exc}")
        try:
            arguments = tool.spec.arguments_model.model_validate(raw)
        except ValidationError as exc:
            return ToolResult.failure(
                "invalid_arguments",
                "Tool arguments do not match the schema.",
                details={"errors": json.loads(exc.json())},
            )
        try:
            decision = self.policy.decision(call, arguments, context)
            approved_shell = False
            if decision.approval is not None:
                if not await approve(decision.approval):
                    return ToolResult.failure(
                        "permission_denied",
                        "The user denied this tool call.",
                        status="denied",
                        details={"tool": call.name},
                    )
                approved_shell = call.name == "run_command"
            cancel_event = threading.Event()
            execution_context = replace(
                context, cancel_event=cancel_event, use_shell=approved_shell
            )
            timeout = (
                arguments.timeout_seconds
                if isinstance(arguments, RunCommandArgs)
                else context.limits.file_timeout_seconds
            )
            try:
                async with asyncio.timeout(timeout):
                    result = await tool.execute(arguments, execution_context)
            except TimeoutError:
                cancel_event.set()
                result = ToolResult.failure(
                    "timeout", "Tool execution exceeded its timeout.", status="timeout", details={"seconds": timeout}
                )
            elapsed = int((time.monotonic() - started) * 1000)
            return redact_result(replace(result, duration_ms=elapsed), context.sensitive_values)
        except asyncio.CancelledError:
            raise
        except ToolExecutionError as exc:
            status = "timeout" if exc.code == "timeout" else "cancelled" if exc.code == "cancelled" else "error"
            result = ToolResult.failure(
                exc.code,
                str(exc),
                status=status,  # type: ignore[arg-type]
                details=exc.details,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return redact_result(result, context.sensitive_values)
        except (OSError, PermissionError) as exc:
            result = ToolResult.failure(
                "execution_failed",
                f"Tool execution failed: {exc.__class__.__name__}: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return redact_result(result, context.sensitive_values)
        except Exception:
            return ToolResult.failure(
                "execution_failed",
                "Tool execution failed unexpectedly.",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

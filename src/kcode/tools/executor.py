from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import suppress
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from kcode.tools.base import (
    ApprovalHandler,
    JSONValue,
    PreparedToolCall,
    RunCommandArgs,
    ToolCall,
    ToolContext,
    ToolError,
    ToolExecutionError,
    ToolEffect,
    ToolResult,
)
from kcode.session import AgentMode
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

    def prepare(
        self,
        call: ToolCall,
        context: ToolContext,
        mode: AgentMode = AgentMode.DO,
    ) -> PreparedToolCall:
        tool = self.registry.get(call.name)
        if tool is None:
            return PreparedToolCall(
                call,
                None,
                None,
                ToolEffect.READ_ONLY,
                error=ToolResult.failure(
                    "unknown_tool", f"Unknown tool: {call.name}", details={"tool": call.name}
                ),
            )
        try:
            raw = json.loads(call.arguments_json)
            if not isinstance(raw, dict):
                raise ValueError("Arguments must be a JSON object.")
        except (json.JSONDecodeError, ValueError) as exc:
            return PreparedToolCall(
                call,
                tool,
                None,
                tool.spec.effect or ToolEffect.SIDE_EFFECT,
                error=ToolResult.failure(
                    "invalid_arguments", f"Invalid tool arguments JSON: {exc}"
                ),
            )
        try:
            arguments = tool.spec.arguments_model.model_validate(raw)
        except ValidationError as exc:
            return PreparedToolCall(
                call,
                tool,
                None,
                tool.spec.effect or ToolEffect.SIDE_EFFECT,
                error=ToolResult.failure(
                    "invalid_arguments",
                    "Tool arguments do not match the schema.",
                    details={"errors": json.loads(exc.json())},
                ),
            )
        try:
            effect = tool.spec.effect
            if effect is None:
                command = str(getattr(arguments, "command"))
                effect = (
                    ToolEffect.READ_ONLY
                    if self.policy.is_read_only_command(command)
                    else ToolEffect.SIDE_EFFECT
                )
            if mode == AgentMode.PLAN and effect == ToolEffect.SIDE_EFFECT:
                return PreparedToolCall(
                    call,
                    tool,
                    arguments,
                    effect,
                    error=ToolResult.failure(
                        "plan_mode_denied",
                        "Plan Mode only allows read-only tools and commands.",
                        status="denied",
                        details={"tool": call.name},
                    ),
                )
            decision = self.policy.decision(call, arguments, context)
            return PreparedToolCall(
                call,
                tool,
                arguments,
                effect,
                approval=decision.approval,
            )
        except ToolExecutionError as exc:
            return PreparedToolCall(
                call,
                tool,
                arguments,
                tool.spec.effect or ToolEffect.SIDE_EFFECT,
                error=ToolResult.failure(exc.code, str(exc), details=exc.details),
            )
        except (OSError, PermissionError) as exc:
            return PreparedToolCall(
                call,
                tool,
                arguments,
                tool.spec.effect or ToolEffect.SIDE_EFFECT,
                error=ToolResult.failure(
                    "execution_failed", f"Cannot prepare tool call: {exc.__class__.__name__}: {exc}"
                ),
            )

    async def execute_prepared(
        self,
        prepared: PreparedToolCall,
        context: ToolContext,
        approve: ApprovalHandler,
        cancel_event: asyncio.Event | None = None,
    ) -> ToolResult:
        started = time.monotonic()
        if prepared.error is not None:
            return prepared.error
        assert prepared.tool is not None and prepared.arguments is not None
        try:
            approved_shell = False
            if prepared.approval is not None:
                approval_task = asyncio.create_task(approve(prepared.approval))
                cancel_task = (
                    asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
                )
                if cancel_task is not None:
                    done, _ = await asyncio.wait(
                        (approval_task, cancel_task), return_when=asyncio.FIRST_COMPLETED
                    )
                    if cancel_task in done and cancel_event.is_set():
                        approval_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await approval_task
                        return ToolResult.failure(
                            "cancelled",
                            "Tool approval was cancelled.",
                            status="cancelled",
                            details={"tool": prepared.call.name},
                        )
                    cancel_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await cancel_task
                if not await approval_task:
                    return ToolResult.failure(
                        "permission_denied",
                        "The user denied this tool call.",
                        status="denied",
                        details={"tool": prepared.call.name},
                    )
                approved_shell = prepared.call.name == "run_command"
            thread_cancel_event = threading.Event()
            execution_context = replace(
                context, cancel_event=thread_cancel_event, use_shell=approved_shell
            )
            timeout = (
                prepared.arguments.timeout_seconds
                if isinstance(prepared.arguments, RunCommandArgs)
                else context.limits.file_timeout_seconds
            )
            execution_task = asyncio.create_task(
                prepared.tool.execute(prepared.arguments, execution_context)
            )
            cancellation_task = (
                asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
            )
            waiters = (execution_task,) if cancellation_task is None else (execution_task, cancellation_task)
            done, _ = await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if execution_task in done:
                result = await execution_task
            elif cancellation_task is not None and cancellation_task in done and cancel_event.is_set():
                thread_cancel_event.set()
                if prepared.call.name in {"write_file", "edit_file"}:
                    result = await execution_task
                else:
                    execution_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await execution_task
                    result = ToolResult.failure(
                        "cancelled",
                        "Tool execution was cancelled.",
                        status="cancelled",
                        details={"tool": prepared.call.name},
                    )
            else:
                thread_cancel_event.set()
                execution_task.cancel()
                with suppress(asyncio.CancelledError):
                    await execution_task
                result = ToolResult.failure(
                    "timeout", "Tool execution exceeded its timeout.", status="timeout", details={"seconds": timeout}
                )
            if cancellation_task is not None:
                cancellation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cancellation_task
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

    async def execute(
        self,
        call: ToolCall,
        context: ToolContext,
        approve: ApprovalHandler,
    ) -> ToolResult:
        return await self.execute_prepared(self.prepare(call, context), context, approve)

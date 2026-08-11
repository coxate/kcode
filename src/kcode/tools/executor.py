from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import suppress
from dataclasses import replace

from pydantic import ValidationError

from kcode.permissions.commands import redact_preview, tool_permission_info
from kcode.permissions.config import LocalPermissionStore
from kcode.permissions.engine import PermissionEngine
from kcode.permissions.models import (
    ApprovalChoice,
    PermissionMode,
    PermissionPersistenceError,
    PermissionSource,
    PermissionVerdict,
)
from kcode.tools.base import (
    ApprovalHandler,
    ApprovalRequest,
    JSONValue,
    PreparedToolCall,
    RunCommandArgs,
    ToolCall,
    ToolContext,
    ToolEffect,
    ToolError,
    ToolExecutionError,
    ToolResult,
    ValidatedToolCall,
)
from kcode.tools.registry import ToolRegistry

DENIAL_CODES = {
    PermissionSource.BLACKLIST: "dangerous_command",
    PermissionSource.PLAN_MODE: "plan_mode_denied",
    PermissionSource.SANDBOX: "path_outside_workspace",
    PermissionSource.LOCAL_RULE: "permission_rule_denied",
    PermissionSource.PROJECT_RULE: "permission_rule_denied",
    PermissionSource.USER_RULE: "permission_rule_denied",
}


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
    def __init__(
        self,
        registry: ToolRegistry,
        permissions: PermissionEngine,
        local_store: LocalPermissionStore,
    ) -> None:
        self.registry = registry
        self.permissions = permissions
        self.local_store = local_store

    def validate(self, call: ToolCall) -> ValidatedToolCall:
        tool = self.registry.get(call.name)
        if tool is None:
            return ValidatedToolCall(
                call,
                None,
                None,
                ToolEffect.SIDE_EFFECT,
                error=ToolResult.failure(
                    "unknown_tool", f"Unknown tool: {call.name}", details={"tool": call.name}
                ),
            )
        try:
            raw = json.loads(call.arguments_json)
            if not isinstance(raw, dict):
                raise ValueError("Arguments must be a JSON object.")
        except (json.JSONDecodeError, ValueError) as exc:
            return ValidatedToolCall(
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
            return ValidatedToolCall(
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
        return ValidatedToolCall(
            call,
            tool,
            arguments,
            tool.spec.effect or ToolEffect.SIDE_EFFECT,
        )

    def authorize(
        self,
        validated: ValidatedToolCall,
        context: ToolContext,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> PreparedToolCall:
        if validated.error is not None:
            return PreparedToolCall(
                validated.call,
                validated.tool,
                validated.arguments,
                validated.declared_effect,
                error=validated.error,
            )
        assert validated.tool is not None and validated.arguments is not None
        call = validated.call
        tool = validated.tool
        arguments = validated.arguments
        declared_effect = validated.declared_effect
        try:
            effect = self.permissions.effect_for(call, arguments, mode, declared_effect)
            decision = self.permissions.evaluate(call, arguments, context, mode, declared_effect)
            if decision.verdict == PermissionVerdict.DENY:
                return PreparedToolCall(
                    call,
                    tool,
                    arguments,
                    effect,
                    error=ToolResult.failure(
                        DENIAL_CODES.get(decision.source, "permission_denied"),
                        decision.reason,
                        status="denied",
                        details={
                            "tool": call.name,
                            "source": decision.source.value,
                            **(
                                {"rule": decision.matched_rule}
                                if decision.matched_rule is not None
                                else {}
                            ),
                        },
                    ),
                )
            approval = None
            if decision.verdict == PermissionVerdict.ASK:
                info = tool_permission_info(call.name, arguments, declared_effect)
                preview = call.arguments_json if call.name.startswith("mcp__") else info.raw_value
                approval = ApprovalRequest(
                    call.id,
                    info.friendly_name,
                    redact_preview(preview, context.sensitive_values),
                    decision.reason,
                    decision.permanent_rule or info.friendly_name,
                )
            return PreparedToolCall(
                call,
                tool,
                arguments,
                effect,
                approval=approval,
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

    def rejected(
        self,
        validated: ValidatedToolCall,
        *,
        hook_id: str,
        reason: str,
    ) -> PreparedToolCall:
        return PreparedToolCall(
            validated.call,
            validated.tool,
            validated.arguments,
            validated.declared_effect,
            error=ToolResult.failure(
                "hook_rejected",
                f"[hook {hook_id}] {reason}",
                status="denied",
                details={"tool": validated.call.name, "hook_id": hook_id},
            ),
        )

    def prepare(
        self,
        call: ToolCall,
        context: ToolContext,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> PreparedToolCall:
        return self.authorize(self.validate(call), context, mode)

    async def execute_prepared(
        self,
        prepared: PreparedToolCall,
        context: ToolContext,
        approve: ApprovalHandler,
        cancel_event: asyncio.Event | None = None,
    ) -> ToolResult:
        started = time.monotonic()
        if prepared.error is not None:
            return redact_result(prepared.error, context.sensitive_values)
        assert prepared.tool is not None and prepared.arguments is not None
        try:
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
                choice = await approval_task
                if choice is True:
                    choice = ApprovalChoice.ALLOW_ONCE
                elif choice is False:
                    choice = ApprovalChoice.DENY
                if choice not in {ApprovalChoice.ALLOW_ONCE, ApprovalChoice.ALLOW_ALWAYS}:
                    return ToolResult.failure(
                        "permission_denied",
                        "The user denied this tool call.",
                        status="denied",
                        details={"tool": prepared.call.name},
                    )
                if choice == ApprovalChoice.ALLOW_ALWAYS:
                    if any(
                        secret and secret in prepared.approval.permanent_rule
                        for secret in context.sensitive_values
                    ):
                        return ToolResult.failure(
                            "permission_persist_failed",
                            "A permanent rule containing a sensitive value was not saved.",
                            details={"tool": prepared.call.name},
                        )
                    try:
                        layer = await asyncio.to_thread(
                            self.local_store.append_allow,
                            prepared.approval.permanent_rule,
                        )
                    except PermissionPersistenceError:
                        return ToolResult.failure(
                            "permission_persist_failed",
                            "The permanent permission rule could not be saved safely.",
                            details={"tool": prepared.call.name},
                        )
                    self.permissions.replace_local_layer(layer)
            thread_cancel_event = threading.Event()
            execution_context = replace(
                context,
                cancel_event=thread_cancel_event,
                use_shell=prepared.call.name == "run_command",
            )
            timeout = (
                None
                if prepared.tool.spec.self_managed_timeout
                else (
                    prepared.arguments.timeout_seconds
                    if isinstance(prepared.arguments, RunCommandArgs)
                    else context.limits.command_timeout_seconds
                    if prepared.call.name.startswith("mcp__")
                    else context.limits.file_timeout_seconds
                )
            )
            execution_task = asyncio.create_task(
                prepared.tool.execute(prepared.arguments, execution_context)
            )
            cancellation_task = (
                asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
            )
            waiters = (
                (execution_task,)
                if cancellation_task is None
                else (execution_task, cancellation_task)
            )
            done, _ = await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if execution_task in done:
                result = await execution_task
            elif (
                cancellation_task is not None
                and cancellation_task in done
                and cancel_event.is_set()
            ):
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
                    "timeout",
                    "Tool execution exceeded its timeout.",
                    status="timeout",
                    details={"seconds": timeout or 0},
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
            status = (
                "timeout"
                if exc.code == "timeout"
                else "cancelled"
                if exc.code == "cancelled"
                else "error"
            )
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
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> ToolResult:
        return await self.execute_prepared(self.prepare(call, context, mode), context, approve)

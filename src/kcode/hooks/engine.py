from __future__ import annotations

import asyncio

from kcode.hooks.executor import HookActionExecutor
from kcode.hooks.models import (
    AgentAction,
    CommandAction,
    Hook,
    HookCatalog,
    HookContext,
    HookDispatchResult,
    HookEvent,
    HookSummary,
    HookWarning,
    HttpAction,
    PromptAction,
    ToolRejectedError,
)
from kcode.hooks.parser import expand_template, redact_context
from kcode.hooks.runtime import HookRuntime, HookSession
from kcode.permissions.models import PermissionMode

MAX_REASON_BYTES = 2 * 1024


class HookEngine:
    def __init__(
        self,
        catalog: HookCatalog | None = None,
        runtime: HookRuntime | None = None,
        executor: HookActionExecutor | None = None,
    ) -> None:
        self.catalog = catalog or HookCatalog()
        self.runtime = runtime or HookRuntime()
        self.executor = executor or HookActionExecutor()
        self.sensitive_values: tuple[str, ...] = ()

    def set_catalog(self, catalog: HookCatalog) -> None:
        self.catalog = catalog

    def update_sensitive_values(self, values: tuple[str, ...]) -> None:
        self.sensitive_values = values
        self.executor.update_sensitive_values(values)

    def bind_agent_launcher(self, launcher) -> None:
        self.executor.bind_agent_launcher(launcher)

    def summaries(self) -> tuple[HookSummary, ...]:
        return self.catalog.summaries()

    def _matches(self, hook: Hook, context: HookContext, session: HookSession | None) -> bool:
        return self.runtime.should_run(session, hook) and (
            hook.condition is None or hook.condition.evaluate(context)
        )

    def _side_effect_blocked(self, hook: Hook, context: HookContext) -> bool:
        return context.mode is PermissionMode.PLAN and isinstance(
            hook.action, (CommandAction, HttpAction)
        )

    async def _background(self, hook: Hook, context: HookContext) -> HookWarning | None:
        try:
            result = await self.executor.execute(hook, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return HookWarning(
                "action_failed",
                f"action failed ({type(exc).__name__})",
                hook.id,
                hook.event,
            )
        return result.warning

    async def _execute(
        self, hook: Hook, context: HookContext, session: HookSession | None
    ) -> tuple[str | None, tuple[HookWarning, ...], bool]:
        if hook.action is None:
            return None, (), True
        if self._side_effect_blocked(hook, context):
            return (
                None,
                (
                    HookWarning(
                        "plan_mode",
                        "side-effect action skipped in Plan Mode",
                        hook.id,
                        hook.event,
                    ),
                ),
                False,
            )
        if isinstance(hook.action, AgentAction):
            try:
                result = await self.executor.execute(hook, context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return (
                    None,
                    (
                        HookWarning(
                            "action_failed",
                            f"action failed ({type(exc).__name__})",
                            hook.id,
                            hook.event,
                        ),
                    ),
                    False,
                )
            warnings = (result.warning,) if result.warning is not None else ()
            return None, warnings, result.warning is None
        if hook.run_async:
            if not self.runtime.spawn(self._background(hook, context)):
                return (
                    None,
                    (
                        HookWarning(
                            "async_limit",
                            "at most 8 async Hook actions may run",
                            hook.id,
                            hook.event,
                        ),
                    ),
                    False,
                )
            return None, (), True
        try:
            result = await self.executor.execute(hook, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return (
                None,
                (
                    HookWarning(
                        "action_failed",
                        f"action failed ({type(exc).__name__})",
                        hook.id,
                        hook.event,
                    ),
                ),
                True,
            )
        warnings = (result.warning,) if result.warning is not None else ()
        if isinstance(hook.action, PromptAction):
            prompt_warnings = self.runtime.enqueue_prompts(session, (result.output,))
            if prompt_warnings:
                return (
                    None,
                    tuple(
                        HookWarning(item.code, item.message, hook.id, hook.event)
                        for item in prompt_warnings
                    ),
                    False,
                )
            return result.output, warnings, True
        return None, warnings, True

    async def run_hooks(
        self, context: HookContext, session: HookSession | None = None
    ) -> HookDispatchResult:
        if context.event is HookEvent.PRE_TOOL_USE:
            raise ValueError("pre_tool_use requires run_pre_tool_hooks")
        context = redact_context(context, self.sensitive_values)
        executed: list[str] = []
        prompts: list[str] = []
        warnings: list[HookWarning] = []
        for hook in self.catalog.for_event(context.event):
            if not self._matches(hook, context, session):
                continue
            prompt, hook_warnings, attempted = await self._execute(hook, context, session)
            warnings.extend(hook_warnings)
            if attempted:
                self.runtime.mark_executed(session, hook)
                executed.append(hook.id)
                if prompt is not None:
                    prompts.append(prompt)
        return HookDispatchResult(tuple(executed), tuple(prompts), tuple(warnings))

    async def run_pre_tool_hooks(
        self, context: HookContext, session: HookSession | None = None
    ) -> HookDispatchResult | ToolRejectedError:
        if context.event is not HookEvent.PRE_TOOL_USE:
            raise ValueError("run_pre_tool_hooks only accepts pre_tool_use")
        context = redact_context(context, self.sensitive_values)
        executed: list[str] = []
        prompts: list[str] = []
        warnings: list[HookWarning] = []
        for hook in self.catalog.for_event(HookEvent.PRE_TOOL_USE):
            if not self._matches(hook, context, session):
                continue
            prompt, hook_warnings, attempted = await self._execute(hook, context, session)
            warnings.extend(hook_warnings)
            if attempted or hook.reject:
                self.runtime.mark_executed(session, hook)
                executed.append(hook.id)
            if prompt is not None:
                prompts.append(prompt)
            if hook.reject:
                for warning in warnings:
                    self.runtime.add_warning(warning)
                reason = expand_template(hook.reason or "Tool use rejected", context)
                encoded = reason.encode("utf-8")[:MAX_REASON_BYTES]
                reason = encoded.decode("utf-8", errors="ignore")
                return ToolRejectedError(hook.id, context.tool_name, reason)
        return HookDispatchResult(tuple(executed), tuple(prompts), tuple(warnings))

    async def close(self) -> tuple[HookWarning, ...]:
        warnings = await self.runtime.close()
        await self.executor.close()
        return warnings

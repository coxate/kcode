from __future__ import annotations

import asyncio
import json
import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

from kcode.hooks.models import (
    AgentAction,
    CommandAction,
    Hook,
    HookContext,
    HookWarning,
    HttpAction,
    PromptAction,
)
from kcode.hooks.parser import expand_template

MAX_OUTPUT_BYTES = 32 * 1024


class AgentActionLauncher(Protocol):
    async def launch_hook(
        self,
        *,
        prompt: str,
        subagent_type: str,
        name: str | None,
        mode,
    ): ...


@dataclass(frozen=True, slots=True)
class HookActionResult:
    output: str = ""
    warning: HookWarning | None = None


class HookActionExecutor:
    def __init__(
        self,
        *,
        sensitive_values: tuple[str, ...] = (),
        http_transport: httpx.AsyncBaseTransport | None = None,
        agent_launcher: AgentActionLauncher | None = None,
    ) -> None:
        self.sensitive_values = sensitive_values
        self._http_transport = http_transport
        self._client: httpx.AsyncClient | None = None
        self.agent_launcher = agent_launcher

    def bind_agent_launcher(self, launcher: AgentActionLauncher) -> None:
        self.agent_launcher = launcher

    def update_sensitive_values(self, values: tuple[str, ...]) -> None:
        self.sensitive_values = values

    async def execute(self, hook: Hook, context: HookContext) -> HookActionResult:
        action = hook.action
        if isinstance(action, CommandAction):
            return await self._command(hook, action, context)
        if isinstance(action, PromptAction):
            return HookActionResult(expand_template(action.message, context))
        if isinstance(action, HttpAction):
            return await self._http(hook, action, context)
        if isinstance(action, AgentAction):
            return await self._agent(hook, action, context)
        return HookActionResult()

    async def _agent(
        self,
        hook: Hook,
        action: AgentAction,
        context: HookContext,
    ) -> HookActionResult:
        if context.is_subagent:
            return HookActionResult(
                warning=HookWarning(
                    "agent_recursion",
                    "SubAgent Hooks cannot launch another Agent",
                    hook.id,
                    hook.event,
                )
            )
        if self.agent_launcher is None:
            return HookActionResult(
                warning=HookWarning(
                    "agent_unavailable",
                    "SubAgent launcher is unavailable",
                    hook.id,
                    hook.event,
                )
            )
        prompt = self._redact(expand_template(action.prompt, context))
        try:
            launched = await self.agent_launcher.launch_hook(
                prompt=prompt,
                subagent_type=action.subagent_type,
                name=action.name,
                mode=context.mode,
            )
        except (RuntimeError, ValueError) as exc:
            return HookActionResult(
                warning=HookWarning(
                    "agent_launch_failed",
                    f"SubAgent task was not launched ({type(exc).__name__})",
                    hook.id,
                    hook.event,
                )
            )
        return HookActionResult(f"SubAgent task launched: {launched.task_id}")

    async def _drain(self, stream: asyncio.StreamReader | None) -> bytes:
        if stream is None:
            return b""
        output = bytearray()
        while chunk := await stream.read(4096):
            if len(output) < MAX_OUTPUT_BYTES:
                output.extend(chunk[: MAX_OUTPUT_BYTES - len(output)])
        return bytes(output)

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            await asyncio.wait_for(process.wait(), timeout=0.2)
        except (ProcessLookupError, asyncio.TimeoutError):
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                with suppress(ProcessLookupError):
                    await process.wait()

    def _redact(self, value: str) -> str:
        for secret in self.sensitive_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    async def _command(
        self, hook: Hook, action: CommandAction, context: HookContext
    ) -> HookActionResult:
        command = expand_template(action.command, context, shell_safe=True)
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=context.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            stdout_task = asyncio.create_task(self._drain(process.stdout))
            stderr_task = asyncio.create_task(self._drain(process.stderr))
            try:
                await asyncio.wait_for(process.wait(), timeout=action.timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                await self._terminate(process)
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                if asyncio.current_task() is not None and asyncio.current_task().cancelling():
                    raise
                return HookActionResult(
                    warning=HookWarning(
                        "command_timeout",
                        "command exceeded its timeout",
                        hook.id,
                        hook.event,
                    )
                )
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            output = self._redact((stdout + stderr).decode("utf-8", errors="replace").strip())
            if process.returncode != 0:
                return HookActionResult(
                    output,
                    HookWarning(
                        "command_failed",
                        f"command exited with status {process.returncode}",
                        hook.id,
                        hook.event,
                    ),
                )
            return HookActionResult(output)
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError) as exc:
            return HookActionResult(
                warning=HookWarning(
                    "command_failed",
                    f"command could not start ({type(exc).__name__})",
                    hook.id,
                    hook.event,
                )
            )

    async def _http(self, hook: Hook, action: HttpAction, context: HookContext) -> HookActionResult:
        url = expand_template(action.url, context)
        if urlparse(url).scheme not in {"http", "https"}:
            return HookActionResult(
                warning=HookWarning(
                    "http_url",
                    "rendered URL must use http or https",
                    hook.id,
                    hook.event,
                )
            )
        headers = {key: expand_template(value, context) for key, value in action.headers.items()}
        content: str | None
        if action.body is None:
            content = json.dumps(
                context.to_payload(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            headers.setdefault("content-type", "application/json")
        else:
            content = expand_template(action.body, context)
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    follow_redirects=False,
                    transport=self._http_transport,
                )
            async with self._client.stream(
                action.method, url, headers=headers, content=content, timeout=action.timeout
            ) as response:
                size = 0
                output_limited = False
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_OUTPUT_BYTES:
                        output_limited = True
                        break
                if not 200 <= response.status_code < 300:
                    return HookActionResult(
                        warning=HookWarning(
                            "http_failed",
                            f"HTTP action returned status {response.status_code}",
                            hook.id,
                            hook.event,
                        )
                    )
                if output_limited:
                    return HookActionResult(
                        warning=HookWarning(
                            "http_output_limit",
                            "HTTP response exceeded 32 KiB and was truncated",
                            hook.id,
                            hook.event,
                        )
                    )
            return HookActionResult()
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            return HookActionResult(
                warning=HookWarning(
                    "http_failed", f"HTTP action failed ({type(exc).__name__})", hook.id, hook.event
                )
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

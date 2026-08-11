from __future__ import annotations

from pathlib import Path

from textual.widgets import Input

from kcode.hooks.catalog import HookCatalogBuilder
from kcode.hooks.engine import HookEngine
from kcode.hooks.executor import HookActionExecutor, HookActionResult
from kcode.hooks.models import Hook, HookContext, HookEvent
from kcode.ui.app import KCodeApp


class Provider:
    display_name = "fake"
    model_name = "fake-model"

    async def stream(self, _messages):
        if False:
            yield


class RecordingExecutor(HookActionExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[HookEvent] = []

    async def execute(self, hook: Hook, context: HookContext) -> HookActionResult:
        self.events.append(context.event)
        return HookActionResult(output=context.event.value)


def write_lifecycle_hooks(path: Path) -> None:
    path.write_text(
        "hooks:\n"
        "  - {id: startup, event: startup, action: {type: prompt, message: startup}}\n"
        "  - {id: session-start, event: session_start, action: {type: prompt, message: start}}\n"
        "  - {id: command, event: command_execute, action: {type: prompt, message: command}}\n"
        "  - {id: session-end, event: session_end, action: {type: prompt, message: end}}\n"
        "  - {id: shutdown, event: shutdown, action: {type: prompt, message: shutdown}}\n",
        encoding="utf-8",
    )


async def submit(app: KCodeApp, pilot, text: str) -> None:
    prompt = app.query_one("#prompt", Input)
    prompt.value = text
    await pilot.press("enter")
    await pilot.pause()


async def test_app_lifecycle_order_and_exit_idempotence(tmp_path: Path) -> None:
    user_hooks = tmp_path / "user-hooks.yaml"
    write_lifecycle_hooks(user_hooks)
    executor = RecordingExecutor()
    engine = HookEngine(executor=executor)
    app = KCodeApp(
        Provider(),
        cwd=tmp_path,
        hook_builder=HookCatalogBuilder(tmp_path, user_path=user_hooks),
        hook_engine=engine,
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        await submit(app, pilot, "/clear")
        await submit(app, pilot, "/exit")

    assert executor.events == [
        HookEvent.STARTUP,
        HookEvent.SESSION_START,
        HookEvent.COMMAND_EXECUTE,
        HookEvent.SESSION_END,
        HookEvent.SESSION_START,
        HookEvent.COMMAND_EXECUTE,
        HookEvent.SESSION_END,
        HookEvent.SHUTDOWN,
    ]

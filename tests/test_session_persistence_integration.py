from __future__ import annotations

import os

import pytest

from kcode.config import AgentConfig
from kcode.conversation import EnvironmentMessage, SystemReminderMessage
from kcode.events import (
    AgentStopped,
    AgentStopReason,
    StreamCompleted,
    TextDelta,
    ToolCallDelta,
    TurnNotice,
)
from kcode.history.runtime import SessionCoordinator
from kcode.orchestration import AgentRunner
from kcode.permissions import (
    LocalPermissionStore,
    PermissionEngine,
    PermissionMode,
    empty_permission_settings,
)
from kcode.session import AgentSession
from kcode.tools.base import ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import create_default_registry


async def allow(_request):
    return True


class FakeEnvironmentCollector:
    async def collect(self, _cwd, *, app_version, model):
        return EnvironmentMessage(f"KCode {app_version} model {model}")


class ScriptedProvider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append(tuple(messages))
        answer = self.answers[len(self.requests) - 1]
        yield TextDelta(answer)
        yield StreamCompleted("stop")


def make_runner(tmp_path, provider, runtime) -> AgentRunner:
    registry = create_default_registry()
    settings = empty_permission_settings(tmp_path)
    runner = AgentRunner(
        provider,
        runtime.conversation,
        registry,
        ToolExecutor(
            registry,
            PermissionEngine(settings),
            LocalPermissionStore(settings.layers[0].path),
        ),
        ToolContext(tmp_path),
        allow,
        AgentConfig(),
        environment_collector=FakeEnvironmentCollector(),
        context_manager=runtime.context_manager,
    )
    runner.bind_session(runtime)
    return runner


@pytest.mark.asyncio
async def test_runner_persists_resumes_and_consumes_stale_reminder_once(tmp_path) -> None:
    first_provider = ScriptedProvider(["first answer"])
    first = SessionCoordinator(tmp_path, first_provider)
    first_runner = make_runner(tmp_path, first_provider, first.current)
    events = [event async for event in first_runner.run("first question", AgentSession())]
    assert events[-1] == AgentStopped(AgentStopReason.COMPLETED, 1)
    target_id = first.current.session_id
    assert "first question" in first.current.journal.path.read_text(encoding="utf-8")
    await first.close()

    second_provider = ScriptedProvider(["continued", "again"])
    second = SessionCoordinator(tmp_path, second_provider)
    resumed = await second.resume(target_id)
    second_runner = make_runner(tmp_path, second_provider, resumed.runtime)

    await _drain(second_runner.run("next question", AgentSession()))
    first_request = second_provider.requests[0]
    assert any(
        isinstance(message, SystemReminderMessage) and message.kind == "session_resume"
        for message in first_request
    )
    assert any(getattr(message, "content", "") == "first question" for message in first_request)

    await _drain(second_runner.run("third question", AgentSession()))
    assert not any(
        isinstance(message, SystemReminderMessage) and message.kind == "session_resume"
        for message in second_provider.requests[1]
    )
    await second.close()


async def _drain(iterator) -> list[object]:
    return [item async for item in iterator]


class BatchedToolProvider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append(tuple(messages))
        self.calls += 1
        if self.calls == 1:
            yield ToolCallDelta(0, "read-1", "read_file", '{"path":"source.txt"}')
            yield ToolCallDelta(
                1,
                "write-1",
                "write_file",
                '{"path":"created.txt","content":"done"}',
            )
            yield StreamCompleted("tool_calls")
        else:
            yield TextDelta("finished")
            yield StreamCompleted("stop")


@pytest.mark.asyncio
async def test_runner_flushes_each_committed_tool_batch_and_final_answer(tmp_path) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    provider = BatchedToolProvider()
    coordinator = SessionCoordinator(tmp_path, provider)
    runner = make_runner(tmp_path, provider, coordinator.current)
    writes: list[tuple[str, ...]] = []
    original_write = coordinator.current.journal._write_lines

    def capture(lines) -> None:
        writes.append(tuple(lines))
        original_write(lines)

    coordinator.current.journal._write_lines = capture
    events = [
        event
        async for event in runner.run(
            "use two batches",
            AgentSession(PermissionMode.BYPASS_PERMISSIONS),
        )
    ]
    assert events[-1] == AgentStopped(AgentStopReason.COMPLETED, 2)
    assert len(writes) == 3
    assert "read-1" in "".join(writes[0])
    assert '"tool_call_id":"write-1"' not in "".join(writes[0])
    assert '"tool_call_id":"write-1"' in "".join(writes[1])
    assert '"content":"finished"' in "".join(writes[2])
    await coordinator.close()


@pytest.mark.asyncio
async def test_resume_reminder_survives_tool_iterations_then_disappears(tmp_path) -> None:
    archived_provider = ScriptedProvider(["archived"])
    archived = SessionCoordinator(tmp_path, archived_provider)
    archived_runner = make_runner(tmp_path, archived_provider, archived.current)
    await _drain(archived_runner.run("old question", AgentSession()))
    target_id = archived.current.session_id
    await archived.close()

    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    provider = BatchedToolProvider()
    resumed_coordinator = SessionCoordinator(tmp_path, provider)
    resumed = await resumed_coordinator.resume(target_id)
    runner = make_runner(tmp_path, provider, resumed.runtime)
    await _drain(runner.run("tool turn", AgentSession(PermissionMode.BYPASS_PERMISSIONS)))
    assert len(provider.requests) == 2
    assert all(
        any(
            isinstance(message, SystemReminderMessage) and message.kind == "session_resume"
            for message in request
        )
        for request in provider.requests
    )

    await _drain(runner.run("after reminder", AgentSession()))
    assert not any(
        isinstance(message, SystemReminderMessage) and message.kind == "session_resume"
        for message in provider.requests[2]
    )
    await resumed_coordinator.close()


@pytest.mark.asyncio
async def test_persistence_failure_keeps_conversation_and_repeats_warning(
    tmp_path, monkeypatch
) -> None:
    provider = ScriptedProvider(["answer survives", "second survives"])
    coordinator = SessionCoordinator(tmp_path, provider)
    runner = make_runner(tmp_path, provider, coordinator.current)

    def fail_fsync(_fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    first_events = await _drain(runner.run("first", AgentSession()))
    assert coordinator.current.conversation.snapshot()[0].assistant == "answer survives"
    assert any(
        isinstance(event, TurnNotice) and "persistence is incomplete" in event.message
        for event in first_events
    )

    second_events = await _drain(runner.run("second", AgentSession()))
    assert coordinator.current.conversation.snapshot()[1].assistant == "second survives"
    assert any(
        isinstance(event, TurnNotice) and "persistence is incomplete" in event.message
        for event in second_events
    )
    await coordinator.close()

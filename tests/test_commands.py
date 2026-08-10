from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from kcode.commands import (
    ArgumentPolicy,
    CommandDispatcher,
    CommandRegistrationError,
    CommandRegistry,
    CommandSpec,
    CommandType,
    MemoryInventory,
    SessionInfo,
    StatusSnapshot,
    create_builtin_registry,
)


@dataclass
class FakeHost:
    notices: list[tuple[str, str]] = field(default_factory=list)
    submitted: list[str] = field(default_factory=list)
    mode: str = "default"
    compact_focus: str | None = None

    async def command_notice(self, text: str, style: str = "system") -> None:
        self.notices.append((text, style))

    async def command_submit_user(self, text: str) -> None:
        self.submitted.append(text)

    def command_enter_plan(self) -> None:
        self.mode = "plan"

    def command_enter_do(self) -> bool:
        self.mode = "default"
        return False

    async def command_compact(self, focus: str | None) -> None:
        self.compact_focus = focus

    async def command_clear(self) -> None:
        return None

    def command_resume(self) -> None:
        return None

    async def command_exit(self) -> None:
        return None

    async def command_clear_mcp_trust(self) -> None:
        return None

    def command_status(self) -> StatusSnapshot:
        return StatusSnapshot(self.mode, 2, 3, 4, None, "test-model", "/tmp/project")

    def command_memories(self) -> MemoryInventory:
        return MemoryInventory(False)

    def command_session(self) -> SessionInfo:
        return SessionInfo(False)


def test_parse_plain_slash_case_and_original_arguments() -> None:
    registry = create_builtin_registry()

    assert registry.parse("question") is None
    assert registry.parse("  ") is None
    assert registry.parse("/").name == "help"
    parsed = registry.parse(" /PLAN   保留  内部格式  ")
    assert parsed is not None
    assert parsed.name == "plan"
    assert parsed.args == "保留  内部格式"


def test_builtin_commands_aliases_and_candidates() -> None:
    registry = create_builtin_registry()

    assert len(registry.visible_commands()) == 13
    assert registry.resolve("H").name == "help"
    assert registry.resolve("?").name == "help"
    assert registry.resolve("C").name == "compact"
    assert registry.resolve("P").name == "plan"
    assert registry.resolve("S").name == "status"
    assert [command.name for command in registry.candidates("s")] == [
        "session",
        "status",
    ]


def test_registration_rejects_name_alias_and_case_conflicts() -> None:
    async def handler(_context) -> None:
        return None

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            "alpha",
            ("a",),
            "alpha",
            "/alpha",
            CommandType.LOCAL,
            ArgumentPolicy.NONE,
            handler,
        )
    )
    with pytest.raises(CommandRegistrationError, match="a"):
        registry.register(
            CommandSpec(
                "A",
                (),
                "conflict",
                "/A",
                CommandType.LOCAL,
                ArgumentPolicy.NONE,
                handler,
            )
        )
    with pytest.raises(CommandRegistrationError, match="invalid"):
        CommandRegistry().register(
            CommandSpec(
                "bad/name",
                (),
                "bad",
                "/bad/name",
                CommandType.LOCAL,
                ArgumentPolicy.NONE,
                handler,
            )
        )


@pytest.mark.asyncio
async def test_dispatch_unknown_usage_plan_review_and_compact() -> None:
    registry = create_builtin_registry()
    dispatcher = CommandDispatcher(registry)
    host = FakeHost()

    assert not await dispatcher.dispatch("ordinary text", host)
    assert await dispatcher.dispatch("/nope", host)
    assert "未知命令" in host.notices[-1][0]
    await dispatcher.dispatch("/status extra", host)
    assert host.notices[-1] == ("用法：/status", "error")
    await dispatcher.dispatch("/plan 设计  注册器", host)
    assert host.mode == "plan"
    assert host.submitted[-1] == "设计  注册器"
    await dispatcher.dispatch("/review 并发安全", host)
    assert "额外关注点：并发安全" in host.submitted[-1]
    await dispatcher.dispatch("/compact 只保留 API", host)
    assert host.compact_focus == "只保留 API"


@pytest.mark.asyncio
async def test_help_is_sorted_and_old_mcp_syntax_is_unknown() -> None:
    registry = create_builtin_registry()
    dispatcher = CommandDispatcher(registry)
    host = FakeHost()

    await dispatcher.dispatch("/", host)
    lines = host.notices[-1][0].splitlines()[1:]
    assert lines == sorted(lines)
    assert len(lines) == 13
    await dispatcher.dispatch("/help s", host)
    assert "名称：/status" in host.notices[-1][0]
    await dispatcher.dispatch("/mcp trust clear", host)
    assert "未知命令：/mcp" in host.notices[-1][0]


@pytest.mark.asyncio
async def test_argument_limit_and_handler_errors_become_notices() -> None:
    async def broken(_context) -> None:
        raise RuntimeError("secret details")

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            "broken",
            (),
            "broken",
            "/broken [参数]",
            CommandType.ACTION,
            ArgumentPolicy.OPTIONAL,
            broken,
        )
    )
    registry.freeze()
    host = FakeHost()
    dispatcher = CommandDispatcher(registry)

    await dispatcher.dispatch("/broken " + "x" * 2001, host)
    assert "2000" in host.notices[-1][0]
    await dispatcher.dispatch("/broken", host)
    assert host.notices[-1][0] == "命令 /broken 执行失败：RuntimeError。"

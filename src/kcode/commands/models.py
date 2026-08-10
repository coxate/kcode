from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from kcode.commands.registry import CommandRegistry
    from kcode.skills.models import SkillSummary


class CommandType(StrEnum):
    LOCAL = "local"
    ACTION = "action"
    PROMPT = "prompt"


class ArgumentPolicy(StrEnum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    mode: str
    input_tokens: int | None
    output_tokens: int | None
    tool_count: int
    memory_count: int | None
    model: str
    cwd: str


@dataclass(frozen=True, slots=True)
class MemoryInventory:
    enabled: bool
    user_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionInfo:
    enabled: bool
    session_id: str | None = None
    journal_path: str | None = None


class CommandHost(Protocol):
    async def command_notice(self, text: str, style: str = "system") -> None: ...

    async def command_submit_user(self, text: str, display_text: str | None = None) -> None: ...

    def command_skills(self) -> tuple[SkillSummary, ...]: ...

    async def command_execute_skill(self, name: str, args: str) -> None: ...

    def command_enter_plan(self) -> None: ...

    def command_enter_do(self) -> bool: ...

    async def command_compact(self, focus: str | None) -> None: ...

    async def command_clear(self) -> None: ...

    def command_resume(self) -> None: ...

    async def command_exit(self) -> None: ...

    async def command_clear_mcp_trust(self) -> None: ...

    def command_status(self) -> StatusSnapshot: ...

    def command_memories(self) -> MemoryInventory: ...

    def command_session(self) -> SessionInfo: ...


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    raw: str
    name: str
    args: str


@dataclass(frozen=True, slots=True)
class CommandContext:
    args: str
    host: CommandHost
    registry: CommandRegistry


CommandHandler = Callable[[CommandContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    type: CommandType
    argument_policy: ArgumentPolicy
    handler: CommandHandler
    argument_hint: str | None = None
    hidden: bool = False

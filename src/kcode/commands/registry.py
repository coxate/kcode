from __future__ import annotations

import re
from dataclasses import replace

from kcode.commands.models import (
    ArgumentPolicy,
    CommandContext,
    CommandHost,
    CommandSpec,
    ParsedCommand,
)

MAX_ARGUMENT_LENGTH = 2000
_INVALID_NAME = re.compile(r"[/\s]")


class CommandRegistrationError(ValueError):
    pass


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self._lookup: dict[str, CommandSpec] = {}
        self._frozen = False

    def register(self, command: CommandSpec) -> None:
        if self._frozen:
            raise CommandRegistrationError("command registry is frozen")
        name = self._validate_name(command.name)
        aliases = tuple(self._validate_name(alias) for alias in command.aliases)
        values = (name, *aliases)
        local: set[str] = set()
        for value in values:
            if value in local or value in self._lookup:
                raise CommandRegistrationError(f"command name or alias conflict: {value}")
            local.add(value)
        command = replace(command, name=name, aliases=aliases)
        self._commands[name] = command
        for value in values:
            self._lookup[value] = command

    def freeze(self) -> None:
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen

    def registered_names(self) -> set[str]:
        return set(self._lookup)

    def resolve(self, name: str) -> CommandSpec | None:
        return self._lookup.get(name.casefold())

    def visible_commands(self) -> tuple[CommandSpec, ...]:
        return tuple(
            sorted(
                (command for command in self._commands.values() if not command.hidden),
                key=lambda command: command.name,
            )
        )

    def candidates(self, prefix: str) -> tuple[CommandSpec, ...]:
        normalized = prefix.casefold()
        return tuple(
            command
            for command in self.visible_commands()
            if command.name.casefold().startswith(normalized)
        )

    @staticmethod
    def parse(text: str) -> ParsedCommand | None:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        body = stripped[1:]
        if not body:
            return ParsedCommand(raw=stripped, name="help", args="")
        parts = body.split(maxsplit=1)
        return ParsedCommand(
            raw=stripped,
            name=parts[0].casefold(),
            args=parts[1].strip() if len(parts) == 2 else "",
        )

    @staticmethod
    def _validate_name(value: str) -> str:
        normalized = value.casefold()
        if not normalized or _INVALID_NAME.search(normalized):
            raise CommandRegistrationError(f"invalid command name or alias: {value!r}")
        return normalized


class CommandDispatcher:
    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    async def dispatch(self, text: str, host: CommandHost) -> bool:
        parsed = self.registry.parse(text)
        if parsed is None:
            return False
        command = self.registry.resolve(parsed.name)
        if command is None:
            await host.command_notice(
                f"未知命令：/{parsed.name}。输入 `/help` 查看帮助。",
                "error",
            )
            return True
        if len(parsed.args) > MAX_ARGUMENT_LENGTH:
            await host.command_notice(
                f"命令参数不能超过 {MAX_ARGUMENT_LENGTH} 个字符。用法：{command.usage}",
                "error",
            )
            return True
        if parsed.args and command.argument_policy is ArgumentPolicy.NONE:
            await host.command_notice(f"用法：{command.usage}", "error")
            return True
        if not parsed.args and command.argument_policy is ArgumentPolicy.REQUIRED:
            await host.command_notice(f"用法：{command.usage}", "error")
            return True
        try:
            await command.handler(CommandContext(parsed.args, host, self.registry))
        except Exception as exc:
            await host.command_notice(
                f"命令 /{command.name} 执行失败：{exc.__class__.__name__}。",
                "error",
            )
        return True

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from kcode.conversation import (
    ConversationMessage,
    EnvironmentMessage,
    StableSystemMessage,
    SystemReminderMessage,
)


@dataclass(frozen=True, slots=True)
class PromptSection:
    name: str
    priority: int
    content: str


class SystemPromptBuilder:
    def __init__(self, sections: Sequence[PromptSection]) -> None:
        self._sections = tuple(sections)
        names: set[str] = set()
        priorities: set[int] = set()
        for section in self._sections:
            name = section.name.strip()
            if not name:
                raise ValueError("Prompt section names cannot be empty.")
            if name in names:
                raise ValueError(f"Duplicate prompt section name: {name}.")
            if section.priority in priorities:
                raise ValueError(f"Duplicate prompt section priority: {section.priority}.")
            names.add(name)
            priorities.add(section.priority)

    def build(self) -> str:
        sections = sorted(self._sections, key=lambda item: item.priority, reverse=True)
        return "\n\n".join(
            section.content.strip() for section in sections if section.content.strip()
        )

    def with_content(self, name: str, content: str) -> SystemPromptBuilder:
        if not any(section.name == name for section in self._sections):
            raise KeyError(f"Unknown prompt section: {name}.")
        sections = tuple(
            replace(section, content=content) if section.name == name else section
            for section in self._sections
        )
        return SystemPromptBuilder(sections)

    def content(self, name: str) -> str:
        section = next((item for item in self._sections if item.name == name), None)
        if section is None:
            raise KeyError(f"Unknown prompt section: {name}.")
        return section.content

    def with_appended_content(self, name: str, content: str) -> SystemPromptBuilder:
        current = self.content(name).strip()
        appended = content.strip()
        return self.with_content(
            name,
            "\n\n".join(item for item in (current, appended) if item),
        )


@dataclass(frozen=True, slots=True)
class PromptPackage:
    stable: StableSystemMessage
    environment: EnvironmentMessage
    reminders: tuple[SystemReminderMessage, ...] = ()

    def messages(self) -> tuple[ConversationMessage, ...]:
        return (self.stable, self.environment, *self.reminders)

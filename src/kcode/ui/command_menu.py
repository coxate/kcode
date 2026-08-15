from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from kcode.commands import CommandRegistry, CommandSpec


class CommandMenu(OptionList):
    """Non-focused slash-command suggestions driven by the command registry."""

    can_focus = False

    def __init__(self, registry: CommandRegistry) -> None:
        super().__init__(id="command-menu", compact=True)
        self.registry = registry
        self.commands: tuple[CommandSpec, ...] = ()

    def update_query(self, value: str) -> None:
        if not value.startswith("/") or any(character.isspace() for character in value):
            self.close()
            return
        prefix = value[1:]
        self.commands = self.registry.candidates(prefix)
        if not self.commands:
            self.set_options([Option("无匹配", id=None, disabled=True)])
            self.highlighted = None
            self.styles.height = 1
            self.display = True
            return
        self.set_options(
            [
                Option(
                    f"/{command.name}  {command.description}",
                    id=command.name,
                )
                for command in self.commands
            ]
        )
        selected = 0
        resolved = self.registry.resolve(prefix)
        if resolved is not None:
            for index, command in enumerate(self.commands):
                if command.name == resolved.name:
                    selected = index
                    break
        self.highlighted = selected
        self.styles.height = min(6, len(self.commands))
        self.display = True
        self.scroll_to_highlight()

    def close(self) -> None:
        self.commands = ()
        self.highlighted = None
        self.display = False

    def selected_name(self) -> str | None:
        highlighted = self.highlighted
        if highlighted is None or not (0 <= highlighted < self.option_count):
            return None
        option = self.get_option_at_index(highlighted)
        return option.id if option.id is not None and not option.disabled else None

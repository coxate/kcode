from dataclasses import dataclass
from enum import StrEnum


class CommandKind(StrEnum):
    HELP = "help"
    CLEAR = "clear"
    EXIT = "exit"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Command:
    kind: CommandKind
    raw: str


def parse_command(text: str) -> Command | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    value = stripped.removeprefix("/").lower()
    known = {
        "help": CommandKind.HELP,
        "clear": CommandKind.CLEAR,
        "exit": CommandKind.EXIT,
    }
    return Command(known.get(value, CommandKind.UNKNOWN), stripped)

from dataclasses import dataclass
from enum import StrEnum


class CommandKind(StrEnum):
    HELP = "help"
    CLEAR = "clear"
    EXIT = "exit"
    PLAN = "plan"
    DO = "do"
    COMPACT = "compact"
    MCP_TRUST_CLEAR = "mcp_trust_clear"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Command:
    kind: CommandKind
    raw: str


def parse_command(text: str) -> Command | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    value = " ".join(stripped.removeprefix("/").lower().split())
    known = {
        "help": CommandKind.HELP,
        "clear": CommandKind.CLEAR,
        "exit": CommandKind.EXIT,
        "plan": CommandKind.PLAN,
        "do": CommandKind.DO,
        "compact": CommandKind.COMPACT,
        "mcp trust clear": CommandKind.MCP_TRUST_CLEAR,
    }
    return Command(known.get(value, CommandKind.UNKNOWN), stripped)

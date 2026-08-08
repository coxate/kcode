from kcode.ui.commands import CommandKind, parse_command


def test_local_commands_and_plain_text() -> None:
    assert parse_command("question") is None
    assert parse_command(" /HELP ").kind == CommandKind.HELP
    assert parse_command("/clear").kind == CommandKind.CLEAR
    assert parse_command("/exit").kind == CommandKind.EXIT
    assert parse_command("/plan").kind == CommandKind.PLAN
    assert parse_command("/do").kind == CommandKind.DO
    assert parse_command("/compact").kind == CommandKind.COMPACT
    assert parse_command("/resume").kind == CommandKind.RESUME
    assert parse_command(" /MCP   TRUST clear ").kind == CommandKind.MCP_TRUST_CLEAR
    assert parse_command("/nope").kind == CommandKind.UNKNOWN

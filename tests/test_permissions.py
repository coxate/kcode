from pathlib import Path

import pytest

from kcode.permissions.blacklist import dangerous_command_reason
from kcode.permissions.engine import PermissionEngine
from kcode.permissions.models import (
    PermissionLayer,
    PermissionMode,
    PermissionSettings,
    PermissionSource,
    PermissionVerdict,
)
from kcode.permissions.rules import match_layers, parse_rule, rule_matches
from kcode.permissions.sandbox import SandboxViolation, resolve_sandboxed_path
from kcode.tools.base import (
    EditFileArgs,
    FindFilesArgs,
    ReadFileArgs,
    RunCommandArgs,
    SearchCodeArgs,
    ToolCall,
    ToolContext,
    WriteFileArgs,
)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "sudo rm -fr '/'",
        "sudo -n rm -r -f /",
        "env SAFE=1 rm -rf /",
        "sh -c 'rm -rf /'",
        "echo ok && rm --recursive --force $HOME",
        "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/disk2",
        "cat image > /dev/nvme0n1",
        "cat image>/dev/nvme0n1",
        "> /dev/sda",
        ":(){ :|:& };:",
        "format C:\\",
        "del /s /q C:\\",
    ],
)
def test_dangerous_commands_are_detected(command: str) -> None:
    assert dangerous_command_reason(command) is not None


@pytest.mark.parametrize(
    "command",
    ["echo 'rm -rf /'", "rm -rf ./build", "git status", "echo hello > output.txt"],
)
def test_nearby_safe_commands_are_not_detected(command: str) -> None:
    assert dangerous_command_reason(command) is None


def test_sandbox_resolves_inside_outside_and_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)

    inside = resolve_sandboxed_path("missing/child.txt", workspace)
    assert inside.relative == "missing/child.txt"
    with pytest.raises(SandboxViolation):
        resolve_sandboxed_path("../outside/file.txt", workspace)
    with pytest.raises(SandboxViolation):
        resolve_sandboxed_path("link/file.txt", workspace)


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("read_file", ReadFileArgs(path="../outside.txt")),
        ("write_file", WriteFileArgs(path="../outside.txt", content="")),
        (
            "edit_file",
            EditFileArgs(path="../outside.txt", old_text="a", new_text="b"),
        ),
        ("find_files", FindFilesArgs(root="../outside", pattern="*")),
        ("search_code", SearchCodeArgs(root="../outside", pattern="x")),
    ],
)
def test_every_file_tool_is_sandboxed(tmp_path, tool_name, arguments) -> None:
    engine = _engine(tmp_path)
    decision = engine.evaluate(
        ToolCall(0, "call", tool_name, "{}"),
        arguments,
        ToolContext(tmp_path),
        PermissionMode.DEFAULT,
    )
    assert decision.source == PermissionSource.SANDBOX
    assert decision.verdict == PermissionVerdict.DENY


def test_rule_parser_and_globs() -> None:
    assert parse_rule("Bash").pattern is None
    assert rule_matches(parse_rule("Bash(git *)"), "Bash", "git status")
    assert not rule_matches(parse_rule("Bash(git status)"), "Bash", "git push")
    assert rule_matches(parse_rule("Write(src/**)"), "Write", "src/a/b.py")
    assert not rule_matches(parse_rule("Write(src/*)"), "Write", "src/a/b.py")
    assert rule_matches(parse_rule("Write(file?.txt)"), "Write", "file?.txt")
    with pytest.raises(ValueError):
        parse_rule("Shell(git status)")
    with pytest.raises(ValueError):
        parse_rule("Bash()")


def test_mcp_permission_name_rules() -> None:
    exact = parse_rule("mcp__github__create_issue")
    wildcard = parse_rule("mcp__github__*")
    assert rule_matches(exact, "mcp__github__create_issue", "")
    assert not rule_matches(exact, "mcp__github__list_issues", "")
    assert rule_matches(wildcard, "mcp__github__list_issues", "")
    assert not rule_matches(wildcard, "mcp__slack__list_messages", "")
    with pytest.raises(ValueError):
        parse_rule("external-tool")


def test_layer_priority_and_deny_before_allow(tmp_path: Path) -> None:
    local = PermissionLayer(
        "local",
        tmp_path / "local",
        allow=(parse_rule("Bash(git status)"),),
        deny=(parse_rule("Bash(git *)"),),
    )
    project = PermissionLayer("project", tmp_path / "project", allow=(parse_rule("Bash(git *)"),))
    matched = match_layers((local, project), "Bash", "git status")
    assert matched is not None
    assert matched[0] is False
    assert matched[1] == PermissionSource.LOCAL_RULE


def _engine(tmp_path: Path, *layers: PermissionLayer) -> PermissionEngine:
    if not layers:
        layers = (
            PermissionLayer("local", tmp_path / "local"),
            PermissionLayer("project", tmp_path / "project"),
            PermissionLayer("user", tmp_path / "user"),
        )
    return PermissionEngine(PermissionSettings(tuple(layers), PermissionMode.DEFAULT))


@pytest.mark.parametrize(
    "mode,read,write,command",
    [
        (PermissionMode.DEFAULT, "allow", "ask", "ask"),
        (PermissionMode.ACCEPT_EDITS, "allow", "allow", "ask"),
        (PermissionMode.BYPASS_PERMISSIONS, "allow", "allow", "allow"),
    ],
)
def test_permission_mode_matrix(tmp_path, mode, read, write, command) -> None:
    engine = _engine(tmp_path)
    context = ToolContext(tmp_path)
    decisions = (
        engine.evaluate(ToolCall(0, "r", "read_file", "{}"), ReadFileArgs(path="x"), context, mode),
        engine.evaluate(
            ToolCall(0, "w", "write_file", "{}"),
            WriteFileArgs(path="x", content=""),
            context,
            mode,
        ),
        engine.evaluate(
            ToolCall(0, "b", "run_command", "{}"),
            RunCommandArgs(command="echo ok"),
            context,
            mode,
        ),
    )
    assert tuple(decision.verdict.value for decision in decisions) == (read, write, command)


def test_hard_guards_and_plan_mode_precede_rules(tmp_path: Path) -> None:
    allow_all = PermissionLayer(
        "local",
        tmp_path / "local",
        allow=(parse_rule("Bash"), parse_rule("Write")),
    )
    engine = _engine(tmp_path, allow_all)
    context = ToolContext(tmp_path)
    dangerous = engine.evaluate(
        ToolCall(0, "b", "run_command", "{}"),
        RunCommandArgs(command="rm -rf /"),
        context,
        PermissionMode.BYPASS_PERMISSIONS,
    )
    planned_write = engine.evaluate(
        ToolCall(0, "w", "write_file", "{}"),
        WriteFileArgs(path="x", content=""),
        context,
        PermissionMode.PLAN,
    )
    planned_read = engine.evaluate(
        ToolCall(0, "b", "run_command", "{}"),
        RunCommandArgs(command="git status"),
        context,
        PermissionMode.PLAN,
    )
    assert dangerous.source == PermissionSource.BLACKLIST
    assert planned_write.source == PermissionSource.PLAN_MODE
    assert planned_read.verdict == PermissionVerdict.ALLOW

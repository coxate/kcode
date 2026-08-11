from __future__ import annotations

from pathlib import Path

import pytest

from kcode.hooks.models import HookContext, HookEvent, HookSource
from kcode.hooks.parser import expand_template, parse_condition, parse_hook
from kcode.permissions.models import PermissionMode


def context(**changes) -> HookContext:
    values = {
        "event": HookEvent.PRE_TOOL_USE,
        "session_id": "session",
        "cwd": Path("/tmp/project"),
        "mode": PermissionMode.DEFAULT,
        "tool_name": "write_file",
        "tool_args": {"path": "src/a b.py", "nested": {"enabled": True}},
        "file_path": "src/a b.py",
    }
    values.update(changes)
    return HookContext(**values)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('tool == "write_file"', True),
        ('tool != "read_file"', True),
        ("args.path =~ /a\\s+b\\.py$/", True),
        ('args.path ~= "src/*.py"', True),
        ('tool == "write_file" && args.path ~= "src/*.py"', True),
        ('tool == "read_file" || args.path ~= "src/*.py"', True),
    ],
)
def test_condition_operators(expression: str, expected: bool) -> None:
    assert parse_condition(expression).evaluate(context()) is expected


@pytest.mark.parametrize(
    "expression",
    [
        'tool == "write_file" && args.path ~= "*.py" || event == "pre_tool_use"',
        '(tool == "write_file")',
        'missing == "x"',
        "tool == write_file",
        "args.path =~ /[/",
    ],
)
def test_invalid_conditions_fail_at_load(expression: str) -> None:
    with pytest.raises((ValueError, Exception)):
        parse_condition(expression)


def test_template_expansion_is_single_pass_and_shell_safe() -> None:
    value = expand_template(
        "fmt $FILE_PATH $TOOL_ARGS.path $$PATH $UNKNOWN",
        context(),
        shell_safe=True,
    )
    assert value == "fmt 'src/a b.py' 'src/a b.py' $PATH "
    assert expand_template("$MESSAGE", context(message="$EVENT")) == "$EVENT"


def test_parse_reject_only_and_reject_constraints(tmp_path: Path) -> None:
    hook, warning = parse_hook(
        {
            "id": "protect-lock",
            "event": "pre_tool_use",
            "if": 'args.path ~= "**/package-lock.json"',
            "reject": True,
            "reason": "use npm install",
        },
        HookSource.PROJECT,
        tmp_path / "hooks.yaml",
        0,
    )
    assert warning is None
    assert hook is not None and hook.action is None and hook.reject

    hook, warning = parse_hook(
        {
            "id": "bad",
            "event": "post_tool_use",
            "action": {"type": "agent", "prompt": "x"},
        },
        HookSource.USER,
        tmp_path / "hooks.yaml",
        0,
    )
    assert hook is None
    assert warning is not None


def test_invalid_hook_warning_does_not_include_action_body(tmp_path: Path) -> None:
    secret = "DO-NOT-LEAK-THIS-HOOK-BODY"
    hook, warning = parse_hook(
        {
            "id": "bad-action",
            "event": "startup",
            "action": {"type": "agent", "prompt": secret},
        },
        HookSource.USER,
        tmp_path / "hooks.yaml",
        0,
    )
    assert hook is None and warning is not None
    assert secret not in warning.render()


@pytest.mark.parametrize(
    "changes",
    [
        {"once": "true"},
        {"reject": 1},
        {"action": {"type": "command", "command": "true", "timeout": "1"}},
    ],
)
def test_boolean_and_timeout_fields_do_not_coerce_types(tmp_path: Path, changes) -> None:
    value = {
        "id": "strict-types",
        "event": "startup",
        "action": {"type": "prompt", "message": "x"},
        **changes,
    }
    hook, warning = parse_hook(
        value,
        HookSource.USER,
        tmp_path / "hooks.yaml",
        0,
    )
    assert hook is None and warning is not None


def test_all_fifteen_events_are_declared() -> None:
    assert len(HookEvent) == 15

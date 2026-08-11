from pathlib import Path

import pytest
from pydantic import ValidationError

from kcode.subagents.models import AgentMeta, AgentSource
from kcode.subagents.parser import MAX_AGENT_BYTES, parse_agent


def _write(root: Path, name: str, meta: str, body: str = "Do the task.") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.md"
    path.write_text(f"---\n{meta}\n---\n{body}\n", encoding="utf-8")
    return path


def test_agent_meta_is_strict_and_safe() -> None:
    meta = AgentMeta(name="code-reviewer", description="Review code")
    assert meta.model == "inherit"
    assert meta.tools == ()
    for payload in (
        {"name": "Bad", "description": "x"},
        {"name": "bad--name", "description": "x"},
        {"name": "ok", "description": "two\nlines"},
        {"name": "ok", "description": "x", "unknown": True},
        {"name": "ok", "description": "x", "tools": ["read_file", "read_file"]},
    ):
        with pytest.raises(ValidationError):
            AgentMeta.model_validate(payload)


def test_parse_minimal_and_warning_does_not_leak_body(tmp_path: Path) -> None:
    root = tmp_path / "agents"
    valid = _write(root, "review", "name: review\ndescription: Review code")
    definition, warning = parse_agent(valid, root, AgentSource.USER)
    assert warning is None
    assert definition is not None
    assert definition.body == "Do the task."

    secret = "BODY-MUST-NOT-LEAK"
    invalid = _write(root, "bad", "name: Bad\ndescription: no", secret)
    definition, warning = parse_agent(invalid, root, AgentSource.PROJECT)
    assert definition is None
    assert warning is not None
    assert secret not in warning.render()


def test_agent_parser_enforces_bytes_utf8_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "agents"
    path = _write(root, "large", "name: large\ndescription: Large", "x")
    prefix = path.read_bytes()[:-2]
    path.write_bytes(prefix + b"x" * (MAX_AGENT_BYTES - len(prefix)))
    assert path.stat().st_size == MAX_AGENT_BYTES
    assert parse_agent(path, root, AgentSource.USER)[0] is not None
    path.write_bytes(path.read_bytes() + b"x")
    assert parse_agent(path, root, AgentSource.USER)[1].code == "too_large"

    invalid = root / "utf8.md"
    invalid.write_bytes(b"\xff\xfe")
    assert parse_agent(invalid, root, AgentSource.USER)[1].code == "invalid_utf8"

    target = _write(root, "target", "name: target\ndescription: Target")
    link = root / "linked.md"
    link.symlink_to(target)
    assert parse_agent(link, root, AgentSource.USER)[0] is None

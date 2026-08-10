from pathlib import Path

import pytest
from pydantic import ValidationError

from kcode.skills.models import ForkContext, SkillMeta, SkillMode, SkillSource
from kcode.skills.parser import MAX_SKILL_BYTES, parse_skill, render_skill_prompt


def write_skill(root: Path, name: str, frontmatter: str, body: str = "Follow the SOP.") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return path


def test_skill_meta_is_strict_and_defaults_are_safe() -> None:
    meta = SkillMeta(name="review", description="Review code")
    assert meta.mode is SkillMode.INLINE
    assert meta.fork_context is ForkContext.NONE
    assert meta.allowed_tools == ()

    for payload in (
        {"name": "Bad", "description": "x"},
        {"name": "ok", "description": "two\nlines"},
        {"name": "ok", "description": "x", "unknown": True},
        {"name": "ok", "description": "x", "allowed_tools": ["read_file", "read_file"]},
        {"name": "ok", "description": "x", "fork_context": "recent"},
    ):
        with pytest.raises(ValidationError):
            SkillMeta.model_validate(payload)


def test_parse_minimal_and_render_arguments(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    path = write_skill(
        root,
        "review",
        "name: review\ndescription: Review code",
        "Do $ARGUMENTS now",
    )
    definition, warning = parse_skill(path, root, SkillSource.USER)
    assert warning is None
    assert definition is not None
    assert definition.body == "Do $ARGUMENTS now"
    assert "Do concurrency now" in render_skill_prompt(definition, "concurrency")

    plain = write_skill(root, "test", "name: test\ndescription: Run tests", "Run tests")
    parsed, _ = parse_skill(plain, root, SkillSource.USER)
    assert parsed is not None
    assert render_skill_prompt(parsed, "unit").endswith("## User Request\n\nunit")
    assert "User Request" not in render_skill_prompt(parsed, "")


def test_invalid_files_are_skipped_without_body_in_warning(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    secret = "BODY-MUST-NOT-LEAK"
    invalid = write_skill(root, "bad", "name: Bad\ndescription: invalid", secret)
    definition, warning = parse_skill(invalid, root, SkillSource.PROJECT)
    assert definition is None
    assert warning is not None
    assert secret not in warning.render()

    binary = root / "binary" / "SKILL.md"
    binary.parent.mkdir()
    binary.write_bytes(b"---\x00bad")
    assert parse_skill(binary, root, SkillSource.USER)[1].code == "binary"

    utf8 = root / "utf8" / "SKILL.md"
    utf8.parent.mkdir()
    utf8.write_bytes(b"\xff\xfe")
    assert parse_skill(utf8, root, SkillSource.USER)[1].code == "invalid_utf8"


def test_size_boundary_and_symlink_are_enforced(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    path = write_skill(root, "large", "name: large\ndescription: Large", "x")
    prefix = path.read_bytes()[:-2]
    path.write_bytes(prefix + b"x" * (MAX_SKILL_BYTES - len(prefix)))
    assert path.stat().st_size == MAX_SKILL_BYTES
    assert parse_skill(path, root, SkillSource.USER)[0] is not None
    path.write_bytes(path.read_bytes() + b"x")
    assert parse_skill(path, root, SkillSource.USER)[1].code == "too_large"

    target = write_skill(root, "target", "name: target\ndescription: Target")
    linked_dir = root / "linked"
    linked_dir.symlink_to(target.parent, target_is_directory=True)
    definition, warning = parse_skill(linked_dir / "SKILL.md", root, SkillSource.USER)
    assert definition is None
    assert warning is not None

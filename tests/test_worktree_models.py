from pathlib import Path

import pytest

from kcode.worktrees import WorktreeError, WorktreeFinalizationReport, validate_slug


@pytest.mark.parametrize("value", ("a", "demo-1", "1-agent", "a" * 64))
def test_valid_slug(value: str) -> None:
    assert validate_slug(value) == value


@pytest.mark.parametrize(
    "value",
    ("", ".", "..", "Agent", "has space", "a/b", "a\\b", "/tmp/a", "../a", "a" * 65),
)
def test_invalid_slug(value: str) -> None:
    with pytest.raises(WorktreeError, match="slug"):
        validate_slug(value)


def test_report_renders_unknown_values() -> None:
    report = WorktreeFinalizationReport(
        "demo",
        Path("/tmp/demo"),
        "kcode-worktree/demo",
        "a" * 40,
        None,
        None,
        None,
        True,
        "kept",
    )
    rendered = report.render()
    assert "HEAD: unknown" in rendered
    assert "Dirty: unknown" in rendered
    assert "Kept: true" in rendered

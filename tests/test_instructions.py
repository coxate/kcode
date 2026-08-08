from __future__ import annotations

from pathlib import Path

from kcode.instructions import InstructionLoader


def write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_three_layers_are_labeled_low_to_high_and_missing_is_silent(tmp_path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    assert InstructionLoader().load(project, home).content == ""

    write(home / ".kcode/KCODE.md", "user-rule")
    write(project / "KCODE.md", "project-rule")
    write(project / ".kcode/KCODE.md", "local-rule")
    bundle = InstructionLoader().load(project, home)

    assert bundle.content.index("user-rule") < bundle.content.index("project-rule")
    assert bundle.content.index("project-rule") < bundle.content.index("local-rule")
    assert "user < project < project-local" in bundle.content
    assert not bundle.warnings


def test_include_allows_nesting_and_repetition_but_rejects_cycle_and_escape(tmp_path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    write(project / "KCODE.md", "@include <docs/a.md>\n@include <docs/a.md>\n")
    write(project / "docs/a.md", "A\n@include <b.md>\n")
    write(project / "docs/b.md", "B\n@include <a.md>\n")
    write(tmp_path / "outside.md", "SECRET")
    with (project / "docs/escape.md").open("w", encoding="utf-8") as handle:
        handle.write("ignored")
    (project / "docs/escape.md").unlink()
    (project / "docs/escape.md").symlink_to(tmp_path / "outside.md")
    write(project / ".kcode/KCODE.md", "@include <../docs/escape.md>\n")

    bundle = InstructionLoader().load(project, home)

    assert bundle.content.count("A") == 2
    assert bundle.content.count("B") == 2
    assert "SECRET" not in bundle.content
    assert {warning.code for warning in bundle.warnings} == {
        "include_cycle",
        "boundary_escape",
    }


def test_include_depth_six_is_rejected(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write(project / "KCODE.md", "@include <0.md>\n")
    for index in range(6):
        following = f"@include <{index + 1}.md>\n" if index < 5 else "too-deep"
        write(project / f"{index}.md", following)

    bundle = InstructionLoader().load(project, tmp_path / "home")
    assert "too-deep" not in bundle.content
    assert any(warning.code == "include_depth" for warning in bundle.warnings)


def test_bad_files_are_isolated_and_budget_keeps_complete_sources(tmp_path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    write(home / ".kcode/KCODE.md", b"bad-utf8-\xff")
    write(project / "KCODE.md", b"binary\x00data")
    write(project / ".kcode/KCODE.md", "local works")

    bundle = InstructionLoader().load(project, home)
    assert "local works" in bundle.content
    assert {warning.code for warning in bundle.warnings} == {"invalid_utf8", "binary"}

    write(home / ".kcode/KCODE.md", "u" * 350)
    write(project / "KCODE.md", "p" * 350)
    write(project / ".kcode/KCODE.md", "local")
    small = InstructionLoader(max_bytes=400).load(project, home)
    assert len(small.content.encode("utf-8")) <= 400
    assert "u" * 350 not in small.content
    assert "local" in small.content
    assert small.truncated


def test_absolute_and_missing_includes_warn_without_stopping_following_content(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write(
        project / "KCODE.md",
        "@include </tmp/not-allowed>\n@include <missing.md>\nstill-here\n",
    )
    bundle = InstructionLoader().load(project, tmp_path / "home")
    assert "still-here" in bundle.content
    assert {warning.code for warning in bundle.warnings} == {"absolute_include", "unreadable"}

import json
from pathlib import Path

from kcode.tools.base import (
    EditFileArgs,
    FindFilesArgs,
    ReadFileArgs,
    SearchCodeArgs,
    ToolContext,
    WriteFileArgs,
)
from kcode.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
from kcode.tools.command import RunCommandTool
from kcode.tools.base import RunCommandArgs
from kcode.tools.registry import create_default_registry
from kcode.tools.search import FindFilesTool, SearchCodeTool


def test_default_registry_contains_six_tools_and_schemas() -> None:
    registry = create_default_registry()
    assert len(registry) == 6
    assert [item.name for item in registry.definitions()] == [
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "find_files",
        "search_code",
    ]
    assert all(item.parameters["additionalProperties"] is False for item in registry.definitions())


async def test_read_file_chunks_without_overlap(tmp_path: Path) -> None:
    target = tmp_path / "large.txt"
    target.write_text("".join(f"line {index}\n" for index in range(1, 8)), encoding="utf-8")
    context = ToolContext(tmp_path)
    first = await ReadFileTool().execute(ReadFileArgs(path=str(target), max_lines=3), context)
    second = await ReadFileTool().execute(
        ReadFileArgs(path=str(target), start_line=first.data["next_start_line"], max_lines=20),  # type: ignore[index,arg-type]
        context,
    )
    assert first.truncated is True
    assert first.data["content"] + second.data["content"] == target.read_text(encoding="utf-8")  # type: ignore[index,operator]


async def test_write_only_creates_and_edit_requires_unique_match(tmp_path: Path) -> None:
    context = ToolContext(tmp_path)
    target = tmp_path / "sample.txt"
    created = await WriteFileTool().execute(WriteFileArgs(path=str(target), content="one two"), context)
    assert created.status == "success"
    try:
        await WriteFileTool().execute(WriteFileArgs(path=str(target), content="overwrite"), context)
    except Exception as exc:
        assert getattr(exc, "code", None) == "already_exists"
    changed = await EditFileTool().execute(
        EditFileArgs(path=str(target), old_text="two", new_text="three"), context
    )
    assert changed.status == "success"
    assert target.read_text(encoding="utf-8") == "one three"
    try:
        await EditFileTool().execute(
            EditFileArgs(path=str(target), old_text="missing", new_text="x"), context
        )
    except Exception as exc:
        assert getattr(exc, "details", {}).get("matches") == 0


async def test_find_and_search_are_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("none\nneedle\n", encoding="utf-8")
    context = ToolContext(tmp_path)
    found = await FindFilesTool().execute(FindFilesArgs(root=str(tmp_path), pattern="*.py"), context)
    assert [Path(item).name for item in found.data["matches"]] == ["a.py", "b.py"]  # type: ignore[index,union-attr]
    searched = await SearchCodeTool().execute(
        SearchCodeArgs(root=str(tmp_path), pattern="needle", file_pattern="*.py"), context
    )
    assert [(Path(item["path"]).name, item["line"]) for item in searched.data["matches"]] == [  # type: ignore[index,union-attr]
        ("a.py", 2),
        ("b.py", 1),
    ]


async def test_run_command_returns_exit_and_output(tmp_path: Path) -> None:
    result = await RunCommandTool().execute(
        RunCommandArgs(command="printf hello", cwd=str(tmp_path)), ToolContext(tmp_path)
    )
    assert result.data["exit_code"] == 0
    assert result.data["stdout"] == "hello"

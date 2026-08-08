from __future__ import annotations

import time

import pytest

from kcode.conversation import AssistantMessage, ToolResultMessage, UserMessage
from kcode.history.ids import create_session_id
from kcode.history.journal import SessionJournal
from kcode.history.models import SessionMetadata
from kcode.history.store import SessionStore, SessionStoreError
from kcode.tools.base import ToolCall, ToolResult


async def make_session(tmp_path, messages, *, model: str = "model") -> SessionJournal:
    session_id = create_session_id()
    journal = SessionJournal(
        tmp_path,
        SessionMetadata(1, session_id, time.time(), "fake", model),
    )
    assert await journal.append_checkpoint(messages)
    assert await journal.close()
    return journal


@pytest.mark.asyncio
async def test_list_sessions_is_sorted_search_ready_and_ignores_old_artifacts(tmp_path) -> None:
    first = await make_session(tmp_path, (UserMessage("first title"), AssistantMessage("a")))
    second = await make_session(tmp_path, (UserMessage("second\n title"), AssistantMessage("b")))
    old = tmp_path / ".kcode/sessions/1720000000-deadbeef/tool-results"
    old.mkdir(parents=True)

    summaries = SessionStore(tmp_path).list_sessions(exclude_session_id=first.metadata.session_id)
    assert [item.session_id for item in summaries] == [second.metadata.session_id]
    assert summaries[0].title == "second title"
    assert summaries[0].message_count == 2
    assert not summaries[0].busy


@pytest.mark.asyncio
async def test_load_skips_bad_line_repairs_pending_tools_and_keeps_log_unchanged(tmp_path) -> None:
    journal = await make_session(
        tmp_path,
        (
            UserMessage("use tool"),
            AssistantMessage("", (ToolCall(0, "call-1", "read_file", "{}"),)),
            ToolResultMessage("orphan", "other", ToolResult.success({"x": 1})),
        ),
    )
    lines = journal.path.read_text(encoding="utf-8").splitlines()
    lines.insert(2, "{bad json")
    journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = journal.path.read_bytes()

    loaded = SessionStore(tmp_path).load(journal.metadata.session_id)
    assert loaded.skipped_lines == 1
    assert isinstance(loaded.messages[-1], ToolResultMessage)
    assert loaded.messages[-1].tool_call_id == "call-1"
    assert loaded.messages[-1].result.error is not None
    assert loaded.messages[-1].result.error.code == "interrupted"
    assert all(
        not isinstance(message, ToolResultMessage) or message.tool_call_id != "orphan"
        for message in loaded.messages
    )
    assert journal.path.read_bytes() == before
    assert any("invalid journal" in warning for warning in loaded.warnings)


@pytest.mark.asyncio
async def test_header_mismatch_and_unknown_schema_are_rejected(tmp_path) -> None:
    journal = await make_session(tmp_path, (UserMessage("x"), AssistantMessage("y")))
    text = journal.path.read_text(encoding="utf-8")
    journal.path.write_text(text.replace('"schema":1', '"schema":2', 1), encoding="utf-8")
    with pytest.raises(SessionStoreError, match="Invalid session header"):
        SessionStore(tmp_path).load(journal.metadata.session_id)


@pytest.mark.asyncio
async def test_load_complete_tool_chain_round_trips(tmp_path) -> None:
    messages = (
        UserMessage("read"),
        AssistantMessage("", (ToolCall(0, "call-1", "read_file", '{"path":"a"}'),)),
        ToolResultMessage("call-1", "read_file", ToolResult.success({"content": "ok"})),
        AssistantMessage("done"),
    )
    journal = await make_session(tmp_path, messages)
    loaded = SessionStore(tmp_path).load(journal.metadata.session_id)
    assert loaded.messages == messages
    assert loaded.turns[0].user == "read"
    assert loaded.turns[0].assistant == "done"


@pytest.mark.asyncio
async def test_invalid_utf8_message_line_is_skipped_without_losing_other_records(tmp_path) -> None:
    journal = await make_session(tmp_path, (UserMessage("x"), AssistantMessage("y")))
    lines = journal.path.read_bytes().splitlines(keepends=True)
    journal.path.write_bytes(b"".join((lines[0], b"\xff\n", *lines[1:])))
    loaded = SessionStore(tmp_path).load(journal.metadata.session_id)
    assert loaded.skipped_lines == 1
    assert loaded.turns[0].assistant == "y"


def test_session_storage_rejects_symlink_escape(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".kcode").mkdir()
    (tmp_path / ".kcode/sessions").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        SessionStore(tmp_path)


@pytest.mark.asyncio
async def test_missing_final_session_end_reports_possible_abnormal_exit(tmp_path) -> None:
    journal = await make_session(tmp_path, (UserMessage("x"), AssistantMessage("y")))
    lines = journal.path.read_text(encoding="utf-8").splitlines()
    journal.path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    loaded = SessionStore(tmp_path).load(journal.metadata.session_id)
    assert any("exited unexpectedly" in warning for warning in loaded.warnings)

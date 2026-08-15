from __future__ import annotations

import json
from datetime import datetime

import pytest

from kcode.conversation import (
    AssistantMessage,
    EnvironmentMessage,
    ProviderContinuationState,
    StableSystemMessage,
    SystemReminderMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.history.codec import (
    HistoryCodecError,
    decode_message,
    decode_record,
    encode_message,
    encode_record,
)
from kcode.history.ids import create_session_id, session_path, validate_session_id
from kcode.history.models import MessageRecord, SessionRecord, SkillStateRecord
from kcode.tools.base import ToolCall, ToolResult


def test_new_session_id_is_strict_and_path_is_bounded(tmp_path) -> None:
    value = create_session_id(datetime(2026, 8, 8, 10, 30, 0))
    assert value.startswith("20260808-103000-")
    assert validate_session_id(value) == value
    assert session_path(tmp_path, value) == tmp_path / value

    for invalid in ("1720000000-deadbeef", "../escape", "20260808-103000-XYZ1"):
        with pytest.raises(ValueError):
            validate_session_id(invalid)


def test_message_round_trip_covers_tools_unicode_and_errors() -> None:
    messages = (
        UserMessage("你好\n世界"),
        AssistantMessage(
            "",
            (
                ToolCall(0, "call-1", "read_file", '{"path":"中文.md"}'),
                ToolCall(1, "call-2", "run_command", '{"command":"false"}'),
            ),
            ProviderContinuationState("provider-private", {"secret": True}),
        ),
        ToolResultMessage(
            "call-1",
            "read_file",
            ToolResult.success({"content": "内容"}, duration_ms=7, warnings=("large",)),
        ),
        ToolResultMessage(
            "call-2",
            "run_command",
            ToolResult.failure("exit", "failed", details={"code": 1}),
        ),
    )

    encoded = tuple(encode_message(item) for item in messages)
    assert all(item is not None for item in encoded)
    decoded = tuple(decode_message(item) for item in encoded if item is not None)
    expected = tuple(
        AssistantMessage(item.content, item.tool_calls)
        if isinstance(item, AssistantMessage)
        else item
        for item in messages
    )
    assert decoded == expected


def test_system_environment_and_reminders_are_not_persisted() -> None:
    assert encode_message(StableSystemMessage("stable")) is None
    assert encode_message(EnvironmentMessage("env")) is None
    assert encode_message(SystemReminderMessage("plan_mode", "temporary")) is None


def test_record_schema_is_versioned_strict_and_jsonl_safe() -> None:
    header = SessionRecord(
        type="session",
        schema=1,
        session_id="20260808-103000-a1b2",
        created_at=1.0,
        provider="fake",
        model="fake-model",
    )
    assert decode_record(encode_record(header)) == header

    line = json.dumps({"type": "message", "ts": 2, "message": {"kind": "user", "content": "x"}})
    assert isinstance(decode_record(line), MessageRecord)

    with pytest.raises(HistoryCodecError):
        decode_record('{"type":"session","schema":2}')
    with pytest.raises(HistoryCodecError):
        decode_record(
            '{"type":"message","ts":1,"message":{"kind":"user","content":"x","unknown":true}}'
        )


def test_skill_state_record_round_trip_keeps_names_ordered() -> None:
    record = SkillStateRecord(type="skill_state", ts=3.0, names=("review", "test"))
    assert decode_record(encode_record(record)) == record
    assert '"names":["review","test"]' in encode_record(record)

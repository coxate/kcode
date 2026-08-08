from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from kcode.conversation import (
    AssistantMessage,
    ChatMessage,
    ConversationMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.history.models import (
    AssistantMessagePayload,
    JournalRecord,
    MessagePayload,
    MessageRecord,
    ToolCallPayload,
    ToolErrorPayload,
    ToolResultMessagePayload,
    ToolResultPayload,
    UserMessagePayload,
)
from kcode.tools.base import ToolCall, ToolError, ToolResult

_RECORD_ADAPTER = TypeAdapter(JournalRecord)


class HistoryCodecError(ValueError):
    pass


def encode_message(message: ConversationMessage) -> MessagePayload | None:
    if isinstance(message, UserMessage):
        return UserMessagePayload(kind="user", content=message.content)
    if isinstance(message, AssistantMessage):
        calls = tuple(
            ToolCallPayload(
                index=call.index,
                id=call.id,
                name=call.name,
                arguments_json=call.arguments_json,
            )
            for call in message.tool_calls
        )
        return AssistantMessagePayload(
            kind="assistant",
            content=message.content,
            tool_calls=calls,
        )
    if isinstance(message, ToolResultMessage):
        result = message.result
        error = (
            ToolErrorPayload(
                code=result.error.code,
                message=result.error.message,
                details=dict(result.error.details),
            )
            if result.error is not None
            else None
        )
        return ToolResultMessagePayload(
            kind="tool_result",
            tool_call_id=message.tool_call_id,
            tool_name=message.tool_name,
            result=ToolResultPayload(
                status=result.status,
                data=dict(result.data) if result.data is not None else None,
                error=error,
                duration_ms=result.duration_ms,
                truncated=result.truncated,
                warnings=result.warnings,
            ),
        )
    if isinstance(message, ChatMessage) and message.role in {"user", "assistant"}:
        if message.role == "user":
            return UserMessagePayload(kind="user", content=message.content)
        return AssistantMessagePayload(kind="assistant", content=message.content)
    return None


def decode_message(payload: MessagePayload) -> ConversationMessage:
    if isinstance(payload, UserMessagePayload):
        return UserMessage(payload.content)
    if isinstance(payload, AssistantMessagePayload):
        calls = tuple(
            ToolCall(call.index, call.id, call.name, call.arguments_json)
            for call in payload.tool_calls
        )
        return AssistantMessage(payload.content, calls)
    result_payload = payload.result
    error = (
        ToolError(
            result_payload.error.code,
            result_payload.error.message,
            result_payload.error.details,
        )
        if result_payload.error is not None
        else None
    )
    result = ToolResult(
        status=result_payload.status,
        data=result_payload.data,
        error=error,
        duration_ms=result_payload.duration_ms,
        truncated=result_payload.truncated,
        warnings=result_payload.warnings,
    )
    return ToolResultMessage(payload.tool_call_id, payload.tool_name, result)


def encode_record(record: JournalRecord) -> str:
    payload = record.model_dump(mode="json", by_alias=True)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode_record(line: str) -> JournalRecord:
    try:
        raw: Any = json.loads(line)
        return _RECORD_ADAPTER.validate_python(raw)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HistoryCodecError(str(exc)) from exc


def message_record(message: ConversationMessage, timestamp: float) -> MessageRecord | None:
    payload = encode_message(message)
    if payload is None:
        return None
    return MessageRecord(type="message", ts=timestamp, message=payload)

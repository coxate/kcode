from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from kcode.context.models import CompactionResult, StructuredSummary
from kcode.context.usage import estimate_tokens_from_characters, message_character_count
from kcode.conversation import (
    AssistantMessage,
    ChatMessage,
    ConversationMessage,
    EnvironmentMessage,
    StableSystemMessage,
    SystemMessage,
    SystemReminderMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.errors import ProviderError, ProviderErrorKind, is_prompt_too_long_error
from kcode.events import StreamCompleted, TextDelta, ToolCallDelta
from kcode.providers.base import ChatProvider

SUMMARY_KEYS = (
    "goal",
    "confirmed_facts",
    "inferences",
    "unknowns",
    "decisions",
    "files",
    "errors",
    "current_state",
    "pending_tasks",
    "next_steps",
    "artifact_references",
    "history_incomplete",
)


class CompactionError(RuntimeError):
    pass


def _message_record(message: ConversationMessage) -> dict[str, Any]:
    if isinstance(message, ChatMessage):
        return {"type": "chat", "role": message.role, "content": message.content}
    if isinstance(message, UserMessage):
        return {"type": "user", "content": message.content}
    if isinstance(message, AssistantMessage):
        return {
            "type": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "index": call.index,
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments_json,
                }
                for call in message.tool_calls
            ],
        }
    if isinstance(message, ToolResultMessage):
        return {
            "type": "tool_result",
            "tool_use_id": message.tool_call_id,
            "tool_name": message.tool_name,
            "result": message.result.to_dict(),
        }
    if isinstance(message, SystemReminderMessage):
        return {"type": "system_reminder", "kind": message.kind, "content": message.content}
    if isinstance(message, StableSystemMessage):
        return {"type": "stable_system", "content": message.content}
    if isinstance(message, EnvironmentMessage):
        return {"type": "environment", "content": message.content}
    if isinstance(message, SystemMessage):
        return {"type": "system", "content": message.content}
    raise TypeError(f"Unsupported conversation message: {type(message)!r}")


def serialize_messages(messages: Sequence[ConversationMessage]) -> str:
    return json.dumps(
        [_message_record(message) for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    )


def message_groups(
    messages: Sequence[ConversationMessage],
) -> tuple[tuple[int, int], ...]:
    groups: list[tuple[int, int]] = []
    index = 0
    while index < len(messages):
        start = index
        message = messages[index]
        index += 1
        is_user = isinstance(message, UserMessage) or (
            isinstance(message, ChatMessage) and message.role == "user"
        )
        if is_user:
            while index < len(messages):
                candidate = messages[index]
                if isinstance(candidate, UserMessage) or (
                    isinstance(candidate, ChatMessage) and candidate.role == "user"
                ):
                    break
                index += 1
        elif isinstance(message, AssistantMessage) and message.tool_calls:
            call_ids = {call.id for call in message.tool_calls}
            while index < len(messages) and isinstance(messages[index], ToolResultMessage):
                if messages[index].tool_call_id not in call_ids:
                    break
                index += 1
        groups.append((start, index))
    return tuple(groups)


def select_recent_messages(
    messages: Sequence[ConversationMessage],
    *,
    minimum_tokens: int = 10_000,
    minimum_messages: int = 5,
) -> tuple[int, tuple[ConversationMessage, ...]]:
    if not messages:
        return 0, ()
    groups = message_groups(messages)
    token_count = 0
    message_count = 0
    start = len(messages)
    for group_start, group_end in reversed(groups):
        group = messages[group_start:group_end]
        token_count += estimate_tokens_from_characters(
            sum(message_character_count(message) for message in group)
        )
        message_count += len(group)
        start = group_start
        if token_count >= minimum_tokens and message_count >= minimum_messages:
            break
    return start, tuple(messages[start:])


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CompactionError(f"Summary field {field!r} must be a list of strings.")
    return tuple(value)


def _extract_json(text: str) -> Mapping[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1])
            if candidate.lstrip().startswith("json"):
                candidate = candidate.lstrip()[4:].lstrip("\n")
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise CompactionError("Summary response does not contain a JSON object.") from None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise CompactionError("Summary response contains invalid JSON.") from exc
    if not isinstance(value, Mapping):
        raise CompactionError("Summary response must be a JSON object.")
    missing = [key for key in SUMMARY_KEYS if key not in value]
    if missing:
        raise CompactionError(f"Summary response is missing fields: {', '.join(missing)}")
    return value


def parse_structured_summary(text: str) -> StructuredSummary:
    value = _extract_json(text)
    goal = value["goal"]
    current_state = value["current_state"]
    history_incomplete = value["history_incomplete"]
    if not isinstance(goal, str) or not isinstance(current_state, str):
        raise CompactionError("Summary goal and current_state must be strings.")
    if type(history_incomplete) is not bool:
        raise CompactionError("Summary history_incomplete must be a boolean.")
    return StructuredSummary(
        goal=goal,
        confirmed_facts=_string_list(value["confirmed_facts"], "confirmed_facts"),
        inferences=_string_list(value["inferences"], "inferences"),
        unknowns=_string_list(value["unknowns"], "unknowns"),
        decisions=_string_list(value["decisions"], "decisions"),
        files=_string_list(value["files"], "files"),
        errors=_string_list(value["errors"], "errors"),
        current_state=current_state,
        pending_tasks=_string_list(value["pending_tasks"], "pending_tasks"),
        next_steps=_string_list(value["next_steps"], "next_steps"),
        artifact_references=_string_list(value["artifact_references"], "artifact_references"),
        history_incomplete=history_incomplete,
    )


def render_structured_summary(summary: StructuredSummary) -> str:
    value = {
        "goal": summary.goal,
        "confirmed_facts": list(summary.confirmed_facts),
        "inferences": list(summary.inferences),
        "unknowns": list(summary.unknowns),
        "decisions": list(summary.decisions),
        "files": list(summary.files),
        "errors": list(summary.errors),
        "current_state": summary.current_state,
        "pending_tasks": list(summary.pending_tasks),
        "next_steps": list(summary.next_steps),
        "artifact_references": list(summary.artifact_references),
        "history_incomplete": summary.history_incomplete,
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_compaction_prompt(
    messages: Sequence[ConversationMessage],
    *,
    history_incomplete: bool,
) -> str:
    return (
        "Create a structured working-memory summary from only the supplied history.\n"
        "Do not call tools. Do not invent, infer as fact, or claim to have read omitted content.\n"
        "Keep confirmed facts, inferences, and unknowns separate. Preserve user constraints and "
        "artifact references.\n"
        "Return exactly one JSON object with these keys: "
        f"{', '.join(SUMMARY_KEYS)}.\n"
        "All fields except goal, current_state, and history_incomplete are arrays of strings. "
        "goal and current_state are strings; history_incomplete is a boolean.\n"
        f"The caller reports history_incomplete={str(history_incomplete).lower()}; the output must "
        "not change true to false.\n"
        "<provided_history>\n"
        f"{serialize_messages(messages)}\n"
        "</provided_history>"
    )


class CompactionEngine:
    def __init__(self, provider: ChatProvider, *, max_prompt_retries: int = 1) -> None:
        self.provider = provider
        self.max_prompt_retries = max_prompt_retries

    async def compact(
        self,
        messages: Sequence[ConversationMessage],
        *,
        source_start: int = 0,
        history_incomplete: bool = False,
    ) -> CompactionResult:
        original = tuple(messages)
        working = original
        dropped_messages = 0
        retry_count = 0
        before_tokens = estimate_tokens_from_characters(
            sum(message_character_count(message) for message in original)
        )
        while working:
            try:
                text = await self._request(working, history_incomplete or dropped_messages > 0)
                summary = parse_structured_summary(text)
                if history_incomplete or dropped_messages:
                    summary = replace(summary, history_incomplete=True)
                rendered = render_structured_summary(summary)
                return CompactionResult(
                    success=True,
                    summary=summary,
                    rendered_summary=rendered,
                    covered_start=source_start + dropped_messages,
                    covered_end=source_start + len(original),
                    history_incomplete=summary.history_incomplete,
                    before_tokens=before_tokens,
                    after_tokens=estimate_tokens_from_characters(len(rendered)),
                    retry_count=retry_count,
                    dropped_messages=dropped_messages,
                )
            except Exception as exc:
                if (
                    retry_count < self.max_prompt_retries
                    and self._is_prompt_too_long(exc)
                    and len(message_groups(working)) > 1
                ):
                    first_end = message_groups(working)[0][1]
                    working = working[first_end:]
                    dropped_messages += first_end
                    retry_count += 1
                    continue
                return CompactionResult(
                    success=False,
                    summary=None,
                    rendered_summary=None,
                    covered_start=source_start,
                    covered_end=source_start + len(original),
                    history_incomplete=history_incomplete or dropped_messages > 0,
                    before_tokens=before_tokens,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                    retry_count=retry_count,
                    dropped_messages=dropped_messages,
                )
        return CompactionResult(
            success=False,
            summary=None,
            rendered_summary=None,
            covered_start=source_start,
            covered_end=source_start,
            history_incomplete=history_incomplete or dropped_messages > 0,
            before_tokens=before_tokens,
            failure_reason="No history remained after compaction retries.",
            retry_count=retry_count,
            dropped_messages=dropped_messages,
        )

    async def _request(
        self,
        messages: Sequence[ConversationMessage],
        history_incomplete: bool,
    ) -> str:
        prompt = build_compaction_prompt(messages, history_incomplete=history_incomplete)
        text_parts: list[str] = []
        completed = False
        async for event in self.provider.stream(
            (ChatMessage("user", prompt),),
            tools=(),
            tool_choice="none",
        ):
            if isinstance(event, TextDelta):
                text_parts.append(event.text)
            elif isinstance(event, ToolCallDelta):
                raise CompactionError("Summary provider attempted a tool call.")
            elif isinstance(event, StreamCompleted):
                completed = True
        if not completed:
            raise CompactionError("Summary stream ended without completion.")
        text = "".join(text_parts).strip()
        if not text:
            raise CompactionError("Summary provider returned empty text.")
        return text

    @staticmethod
    def _is_prompt_too_long(error: BaseException) -> bool:
        return (
            isinstance(error, ProviderError) and error.kind == ProviderErrorKind.PROMPT_TOO_LONG
        ) or is_prompt_too_long_error(error)

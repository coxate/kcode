from __future__ import annotations

import ast
import re
import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from kcode.hooks.models import (
    CompiledMatcher,
    Condition,
    ConditionGroup,
    ConditionJoin,
    ConditionOperator,
    Hook,
    HookAction,
    HookContext,
    HookEvent,
    HookSource,
    HookWarning,
    HttpAction,
)
from kcode.matching import glob_regex

HOOK_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ATOM = re.compile(r"^([a-z_][a-z0-9_.]*)\s*(==|!=|=~|~=)\s*(.+)$", re.DOTALL)
FIELDS = {"event", "tool", "file_path", "message", "error", "command"}
VARIABLE = re.compile(
    r"\$\$|\$TOOL_ARGS(?:\.[A-Za-z0-9_-]+)+|\$(?:EVENT|TOOL_NAME|FILE_PATH|MESSAGE|ERROR)|\$[A-Z_][A-Z0-9_]*"
)
ACTION_ADAPTER = TypeAdapter(HookAction)


class _ExactMatcher:
    def __init__(self, expected: str, negate: bool = False) -> None:
        self.expected = expected
        self.negate = negate

    def matches(self, actual: str) -> bool:
        result = actual == self.expected
        return not result if self.negate else result


class _RegexMatcher:
    def __init__(self, pattern: str) -> None:
        self.pattern = re.compile(pattern)

    def matches(self, actual: str) -> bool:
        return self.pattern.search(actual) is not None


class _GlobMatcher:
    def __init__(self, pattern: str) -> None:
        self.pattern = glob_regex(pattern, path=True)

    def matches(self, actual: str) -> bool:
        return self.pattern.fullmatch(actual) is not None


class RawHook(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    event: HookEvent
    condition: str | None = Field(default=None, alias="if", max_length=32 * 1024)
    action: dict[str, Any] | None = None
    reject: StrictBool = False
    reason: str | None = Field(default=None, max_length=32 * 1024)
    once: StrictBool = False
    run_async: StrictBool = Field(default=False, alias="async")

    @model_validator(mode="after")
    def validate_combination(self) -> RawHook:
        if not HOOK_ID.fullmatch(self.id):
            raise ValueError("id must match [a-z][a-z0-9_-]{0,63}")
        if self.action is None and not (self.event is HookEvent.PRE_TOOL_USE and self.reject):
            raise ValueError("action is required unless this is a rejecting pre_tool_use hook")
        if self.reject and self.event is not HookEvent.PRE_TOOL_USE:
            raise ValueError("reject is only allowed for pre_tool_use")
        if self.reject and not (self.reason and self.reason.strip()):
            raise ValueError("reject requires a non-empty reason")
        return self


def _split_expression(expression: str) -> tuple[list[str], ConditionJoin]:
    parts: list[str] = []
    operators: list[str] = []
    start = 0
    quote = ""
    regex_literal = False
    escaped = False
    for index, char in enumerate(expression):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if regex_literal:
            if char == "/":
                regex_literal = False
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "/" and expression[:index].rstrip().endswith("=~"):
            regex_literal = True
            continue
        candidate = expression[index : index + 2]
        if candidate in {"&&", "||"}:
            parts.append(expression[start:index].strip())
            operators.append(candidate)
            start = index + 2
    if quote or regex_literal or escaped:
        raise ValueError("unterminated string, regular expression, or escape")
    parts.append(expression[start:].strip())
    if not all(parts):
        raise ValueError("condition contains an empty operand")
    if len(set(operators)) > 1:
        raise ValueError("&& and || cannot be mixed")
    join = ConditionJoin.ANY if operators and operators[0] == "||" else ConditionJoin.ALL
    return parts, join


def _literal(value: str) -> str:
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError) as exc:
        raise ValueError("condition values must be quoted strings") from exc
    if not isinstance(parsed, str):
        raise ValueError("condition values must be strings")
    return parsed


def parse_condition(expression: str) -> ConditionGroup:
    parts, join = _split_expression(expression.strip())
    conditions: list[Condition] = []
    for part in parts:
        match = ATOM.fullmatch(part)
        if match is None:
            raise ValueError(f"invalid condition: {part!r}")
        field, raw_operator, raw_value = match.groups()
        if field not in FIELDS and not field.startswith("args."):
            raise ValueError(f"unknown condition field: {field}")
        operator = ConditionOperator(raw_operator)
        if operator is ConditionOperator.REGEX:
            if len(raw_value) < 2 or not raw_value.startswith("/") or not raw_value.endswith("/"):
                raise ValueError("regular expressions must use /pattern/ syntax")
            expected = raw_value[1:-1].replace(r"\/", "/")
            matcher: CompiledMatcher = _RegexMatcher(expected)
        else:
            expected = _literal(raw_value)
            matcher = (
                _GlobMatcher(expected)
                if operator is ConditionOperator.GLOB
                else _ExactMatcher(expected, operator is ConditionOperator.NOT_EQUAL)
            )
        conditions.append(Condition(field, operator, expected, matcher))
    return ConditionGroup(join, tuple(conditions))


def parse_hook(
    value: object,
    source: HookSource,
    source_path: Path,
    order: int,
) -> tuple[Hook | None, HookWarning | None]:
    hook_id = (
        value.get("id") if isinstance(value, dict) and isinstance(value.get("id"), str) else None
    )
    try:
        raw = RawHook.model_validate(value)
        action = ACTION_ADAPTER.validate_python(raw.action) if raw.action is not None else None
        if isinstance(action, HttpAction):
            method = action.method.upper()
            if not re.fullmatch(r"[A-Z]+", method):
                raise ValueError("http method must contain only letters")
            if "$" not in action.url and urlparse(action.url).scheme not in {"http", "https"}:
                raise ValueError("http url must use http or https")
            action = action.model_copy(update={"method": method})
        if raw.run_async and (
            raw.event is HookEvent.PRE_TOOL_USE
            or raw.reject
            or action is not None
            and action.type == "prompt"
        ):
            raise ValueError("async is not allowed for pre_tool_use, reject, or prompt")
        condition = parse_condition(raw.condition) if raw.condition else None
        return (
            Hook(
                raw.id,
                raw.event,
                condition,
                action,
                raw.reject,
                raw.reason,
                raw.once,
                raw.run_async,
                source,
                source_path,
                order,
            ),
            None,
        )
    except (ValidationError, ValueError, re.error) as exc:
        return None, HookWarning(
            "invalid_hook",
            f"invalid configuration ({type(exc).__name__})",
            hook_id,
        )


def expand_template(template: str, context: HookContext, *, shell_safe: bool = False) -> str:
    fixed = {
        "$EVENT": context.event.value,
        "$TOOL_NAME": context.tool_name,
        "$FILE_PATH": context.file_path,
        "$MESSAGE": context.message,
        "$ERROR": context.error,
    }

    def replace_variable(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "$$":
            return "$"
        if token.startswith("$TOOL_ARGS."):
            value = context.field_value("args." + token[len("$TOOL_ARGS.") :])
        else:
            value = fixed.get(token, "")
        return shlex.quote(value) if shell_safe and value else value

    return VARIABLE.sub(replace_variable, template)


def redact_context(context: HookContext, secrets: tuple[str, ...]) -> HookContext:
    def redact(value: Any) -> Any:
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return replace(
        context,
        tool_args=redact(dict(context.tool_args)),
        file_path=redact(context.file_path),
        message=redact(context.message),
        error=redact(context.error),
        command=redact(context.command),
    )

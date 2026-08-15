from __future__ import annotations

import re
from collections.abc import Iterable

from kcode.matching import glob_regex
from kcode.permissions.models import (
    FriendlyToolName,
    PermissionLayer,
    PermissionRule,
    PermissionSource,
)

FRIENDLY_NAMES = {"Bash", "Read", "Write", "Edit", "Glob", "Grep"}
RULE_PATTERN = re.compile(r"^(Bash|Read|Write|Edit|Glob|Grep)(?:\((.*)\))?$", re.DOTALL)
MCP_RULE_PATTERN = re.compile(r"^mcp__[A-Za-z0-9_*-]+__[A-Za-z0-9_*-]+$")
SOURCE_BY_LAYER: dict[str, PermissionSource] = {
    "local": PermissionSource.LOCAL_RULE,
    "project": PermissionSource.PROJECT_RULE,
    "user": PermissionSource.USER_RULE,
}


def parse_rule(raw: str) -> PermissionRule:
    if not isinstance(raw, str):
        raise ValueError("permission rules must be strings")
    value = raw.strip()
    match = RULE_PATTERN.fullmatch(value)
    if match is None:
        if MCP_RULE_PATTERN.fullmatch(value) is not None:
            return PermissionRule(value, value, None)
        raise ValueError(f"invalid permission rule: {raw!r}")
    pattern = match.group(2)
    if pattern is not None and not pattern:
        raise ValueError("permission rule patterns cannot be empty")
    return PermissionRule(value, match.group(1), pattern)  # type: ignore[arg-type]


def parse_rules(values: Iterable[str]) -> tuple[PermissionRule, ...]:
    return tuple(parse_rule(value) for value in values)


def rule_matches(rule: PermissionRule, tool_name: FriendlyToolName, value: str) -> bool:
    if rule.tool_name.startswith("mcp__"):
        return (
            glob_regex(rule.tool_name, path=False, question_mark=False).fullmatch(tool_name)
            is not None
        )
    if rule.tool_name != tool_name:
        return False
    if rule.pattern is None:
        return True
    if "*" not in rule.pattern:
        return value == rule.pattern
    return (
        glob_regex(
            rule.pattern,
            path=tool_name != "Bash",
            question_mark=False,
        ).fullmatch(value)
        is not None
    )


def match_layers(
    layers: tuple[PermissionLayer, ...], tool_name: FriendlyToolName, value: str
) -> tuple[bool, PermissionSource, PermissionRule] | None:
    for layer in layers:
        source = SOURCE_BY_LAYER[layer.name]
        for rule in layer.deny:
            if rule_matches(rule, tool_name, value):
                return False, source, rule
        for rule in layer.allow:
            if rule_matches(rule, tool_name, value):
                return True, source, rule
    return None

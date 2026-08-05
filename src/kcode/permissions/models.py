from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal


class PermissionMode(StrEnum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"


class PermissionVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionSource(StrEnum):
    BLACKLIST = "blacklist"
    PLAN_MODE = "plan_mode"
    SANDBOX = "sandbox"
    LOCAL_RULE = "local_rule"
    PROJECT_RULE = "project_rule"
    USER_RULE = "user_rule"
    MODE = "mode"
    USER = "user"
    PERSISTENCE = "persistence"


class ApprovalChoice(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


class ToolCategory(StrEnum):
    READ = "read"
    WRITE = "write"
    COMMAND = "command"


FriendlyToolName = Literal["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
LayerName = Literal["local", "project", "user"]


@dataclass(frozen=True, slots=True)
class PermissionRule:
    raw: str
    tool_name: FriendlyToolName
    pattern: str | None


@dataclass(frozen=True, slots=True)
class PermissionLayer:
    name: LayerName
    path: Path
    default_mode: PermissionMode | None = None
    allow: tuple[PermissionRule, ...] = ()
    deny: tuple[PermissionRule, ...] = ()


@dataclass(frozen=True, slots=True)
class PermissionSettings:
    layers: tuple[PermissionLayer, ...]
    initial_mode: PermissionMode
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    verdict: PermissionVerdict
    source: PermissionSource
    reason: str
    matched_rule: str | None = None
    permanent_rule: str | None = None


class PermissionPersistenceError(Exception):
    """A local permission rule could not be safely persisted."""

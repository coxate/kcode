from __future__ import annotations

import threading

from kcode.permissions.blacklist import dangerous_command_reason
from kcode.permissions.commands import is_read_only_command, tool_permission_info
from kcode.permissions.models import (
    PermissionDecision,
    PermissionLayer,
    PermissionMode,
    PermissionSettings,
    PermissionSource,
    PermissionVerdict,
    ToolCategory,
)
from kcode.permissions.rules import match_layers
from kcode.permissions.sandbox import SandboxViolation, resolve_sandboxed_path
from kcode.tools.base import ToolArguments, ToolCall, ToolContext, ToolEffect

MODE_MATRIX = {
    PermissionMode.DEFAULT: {
        ToolCategory.READ: PermissionVerdict.ALLOW,
        ToolCategory.WRITE: PermissionVerdict.ASK,
        ToolCategory.COMMAND: PermissionVerdict.ASK,
    },
    PermissionMode.ACCEPT_EDITS: {
        ToolCategory.READ: PermissionVerdict.ALLOW,
        ToolCategory.WRITE: PermissionVerdict.ALLOW,
        ToolCategory.COMMAND: PermissionVerdict.ASK,
    },
    PermissionMode.BYPASS_PERMISSIONS: {
        ToolCategory.READ: PermissionVerdict.ALLOW,
        ToolCategory.WRITE: PermissionVerdict.ALLOW,
        ToolCategory.COMMAND: PermissionVerdict.ALLOW,
    },
    PermissionMode.PLAN: {
        ToolCategory.READ: PermissionVerdict.ALLOW,
        ToolCategory.WRITE: PermissionVerdict.DENY,
        ToolCategory.COMMAND: PermissionVerdict.ALLOW,
    },
}


class PermissionEngine:
    def __init__(self, settings: PermissionSettings) -> None:
        self._layers = settings.layers
        self._lock = threading.RLock()

    @property
    def layers(self) -> tuple[PermissionLayer, ...]:
        with self._lock:
            return self._layers

    def replace_local_layer(self, layer: PermissionLayer) -> None:
        if layer.name != "local":
            raise ValueError("only the local permission layer can be replaced")
        with self._lock:
            self._layers = (layer, *(item for item in self._layers if item.name != "local"))

    def effect_for(
        self, call: ToolCall, arguments: ToolArguments, mode: PermissionMode
    ) -> ToolEffect:
        info = tool_permission_info(call.name, arguments)
        if info.category == ToolCategory.READ:
            return ToolEffect.READ_ONLY
        if (
            info.category == ToolCategory.COMMAND
            and mode == PermissionMode.PLAN
            and is_read_only_command(info.raw_value)
        ):
            return ToolEffect.READ_ONLY
        return ToolEffect.SIDE_EFFECT

    def evaluate(
        self,
        call: ToolCall,
        arguments: ToolArguments,
        context: ToolContext,
        mode: PermissionMode,
    ) -> PermissionDecision:
        info = tool_permission_info(call.name, arguments)

        if info.category == ToolCategory.COMMAND:
            reason = dangerous_command_reason(info.raw_value)
            if reason is not None:
                return PermissionDecision(
                    PermissionVerdict.DENY,
                    PermissionSource.BLACKLIST,
                    f"KCode blocked a dangerous command: {reason}.",
                )

        if mode == PermissionMode.PLAN:
            if info.category == ToolCategory.WRITE:
                return PermissionDecision(
                    PermissionVerdict.DENY,
                    PermissionSource.PLAN_MODE,
                    "Plan Mode does not allow file changes.",
                )
            if info.category == ToolCategory.COMMAND and not is_read_only_command(info.raw_value):
                return PermissionDecision(
                    PermissionVerdict.DENY,
                    PermissionSource.PLAN_MODE,
                    "Plan Mode only allows strictly read-only commands.",
                )

        match_value = info.raw_value
        if info.category != ToolCategory.COMMAND:
            try:
                sandboxed = resolve_sandboxed_path(info.raw_value, context.workspace_root)
            except SandboxViolation:
                return PermissionDecision(
                    PermissionVerdict.DENY,
                    PermissionSource.SANDBOX,
                    "The requested path is outside the KCode project sandbox.",
                )
            match_value = sandboxed.relative

        matched = match_layers(self.layers, info.friendly_name, match_value)
        if matched is not None:
            allowed, source, rule = matched
            return PermissionDecision(
                PermissionVerdict.ALLOW if allowed else PermissionVerdict.DENY,
                source,
                f"Permission rule {'allowed' if allowed else 'denied'} this tool call.",
                matched_rule=rule.raw,
            )

        verdict = MODE_MATRIX[mode][info.category]
        permanent_rule = (
            f"{info.friendly_name}({match_value})" if verdict == PermissionVerdict.ASK else None
        )
        return PermissionDecision(
            verdict,
            PermissionSource.MODE,
            f"Permission mode {mode.value} requires user confirmation."
            if verdict == PermissionVerdict.ASK
            else f"Permission mode {mode.value} allowed this tool call.",
            permanent_rule=permanent_rule,
        )

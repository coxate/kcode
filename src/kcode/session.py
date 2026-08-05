from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from kcode.permissions.models import PermissionMode

MODE_CYCLE = (
    PermissionMode.DEFAULT,
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS_PERMISSIONS,
)


class AgentMode(StrEnum):
    """Compatibility names for the pre-0.4 two-mode API."""

    DO = "default"
    PLAN = "plan"


@dataclass(slots=True)
class AgentSession:
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    latest_plan: str | None = None
    initial_mode: PermissionMode | None = None
    _approved_plan: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.permission_mode = PermissionMode(self.permission_mode)
        if self.initial_mode is None:
            self.initial_mode = self.permission_mode

    @property
    def mode(self) -> PermissionMode:
        return self.permission_mode

    def set_mode(self, mode: PermissionMode | AgentMode) -> None:
        self.permission_mode = PermissionMode(mode)

    def cycle_mode(self) -> PermissionMode:
        index = MODE_CYCLE.index(self.permission_mode)
        self.permission_mode = MODE_CYCLE[(index + 1) % len(MODE_CYCLE)]
        return self.permission_mode

    def record_plan(self, text: str) -> None:
        plan = text.strip()
        if plan:
            self.latest_plan = plan

    def approve_plan(self) -> bool:
        self.permission_mode = PermissionMode.DEFAULT
        self._approved_plan = self.latest_plan
        self.latest_plan = None
        return self._approved_plan is not None

    def consume_approved_plan(self) -> str | None:
        plan = self._approved_plan
        self._approved_plan = None
        return plan

    def consume_plan(self) -> str | None:
        """Compatibility helper retained for callers of the 0.3 session API."""
        if self.permission_mode == PermissionMode.PLAN:
            return None
        plan = self.latest_plan
        self.latest_plan = None
        return plan

    def clear(self) -> None:
        self.permission_mode = self.initial_mode or PermissionMode.DEFAULT
        self.latest_plan = None
        self._approved_plan = None

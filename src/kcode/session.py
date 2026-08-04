from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AgentMode(StrEnum):
    PLAN = "plan"
    DO = "do"


@dataclass(slots=True)
class AgentSession:
    mode: AgentMode = AgentMode.DO
    latest_plan: str | None = None

    def set_mode(self, mode: AgentMode) -> None:
        self.mode = mode

    def record_plan(self, text: str) -> None:
        plan = text.strip()
        if plan:
            self.latest_plan = plan

    def consume_plan(self) -> str | None:
        if self.mode != AgentMode.DO:
            return None
        plan = self.latest_plan
        self.latest_plan = None
        return plan

    def clear(self) -> None:
        self.mode = AgentMode.DO
        self.latest_plan = None

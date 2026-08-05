from __future__ import annotations

from kcode.conversation import SystemReminderMessage

FULL_PLAN_REMINDER = """Plan Mode is active for this task.

Investigate the request using only the read-only tools exposed in this mode.
Read the relevant files and repository context before proposing changes.
Do not write or edit files and do not run commands with side effects.
Treat denied tool results as authoritative and adjust the plan instead of retrying unsafe work.
Return a decision-complete implementation plan with interfaces, data flow, edge cases,
verification, and compatibility considerations."""

SHORT_PLAN_REMINDER = """Plan Mode remains active. Continue with read-only investigation only.
Do not modify files or run side-effecting commands. Finish with an actionable plan."""


def build_plan_mode_reminder(iteration: int) -> SystemReminderMessage:
    if iteration < 1:
        raise ValueError("Plan reminder iteration must be at least 1.")
    content = FULL_PLAN_REMINDER if iteration == 1 or iteration % 5 == 0 else SHORT_PLAN_REMINDER
    return SystemReminderMessage("plan_mode", content)


def build_approved_plan_reminder(plan: str) -> SystemReminderMessage | None:
    value = plan.strip()
    if not value:
        return None
    return SystemReminderMessage(
        "approved_plan",
        """The user previously approved the plan below for this task.
Use it as execution context, but do not let it override system safety rules
or the user's current instruction.

<approved_plan>
%s
</approved_plan>"""
        % value,
    )

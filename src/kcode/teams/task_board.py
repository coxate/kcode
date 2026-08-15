from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import replace

from kcode.teams.models import (
    TeamError,
    TeamTask,
    TeamTaskStatus,
    make_team_task_id,
)

UNSET = object()


class TaskBoard:
    def __init__(self, tasks: dict[str, TeamTask] | None = None) -> None:
        self.tasks = tasks if tasks is not None else {}

    @staticmethod
    def _validate_text(value: str, field: str) -> str:
        if not value or not value.strip():
            raise TeamError("invalid_team_task", f"Team task {field} must not be empty.")
        return value

    @staticmethod
    def _validate_assignee(
        assignee: str | None,
        valid_assignees: Iterable[str],
        inactive_assignees: Iterable[str] = (),
        *,
        entering_progress: bool = False,
    ) -> None:
        if assignee is None:
            return
        if assignee not in set(valid_assignees) | {"lead"}:
            raise TeamError("invalid_assignee", "Team task assignee is not a Team participant.")
        if entering_progress and assignee in set(inactive_assignees):
            raise TeamError("inactive_assignee", "A stopped or failed member cannot own new work.")

    def create(
        self,
        *,
        title: str,
        description: str,
        assignee: str | None,
        blocked_by: Iterable[str],
        created_by: str,
        valid_assignees: Iterable[str],
    ) -> TeamTask:
        title = self._validate_text(title, "title")
        description = self._validate_text(description, "description")
        self._validate_assignee(assignee, valid_assignees)
        dependencies = frozenset(blocked_by)
        if any(item not in self.tasks for item in dependencies):
            raise TeamError("unknown_team_task", "A Team task dependency does not exist.")
        now = time.time()
        task = TeamTask(
            make_team_task_id(),
            title,
            description,
            TeamTaskStatus.PENDING,
            assignee,
            dependencies,
            created_by,
            now,
            now,
        )
        self.tasks[task.id] = task
        return task

    def ready(self, task: TeamTask) -> bool:
        return all(self.tasks[item].status is TeamTaskStatus.COMPLETED for item in task.blocked_by)

    def list(self, status: TeamTaskStatus | None = None) -> tuple[TeamTask, ...]:
        return tuple(
            item
            for item in sorted(self.tasks.values(), key=lambda value: value.created_at)
            if status is None or item.status is status
        )

    def _assert_acyclic(self, tasks: Mapping[str, TeamTask]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise TeamError("task_dependency_cycle", "Team task dependencies must be acyclic.")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in tasks[task_id].blocked_by:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in tasks:
            visit(task_id)

    def update(
        self,
        task_id: str,
        *,
        status: TeamTaskStatus | None = None,
        assignee: str | None | object = UNSET,
        add_blocked_by: Iterable[str] = (),
        remove_blocked_by: Iterable[str] = (),
        valid_assignees: Iterable[str],
        inactive_assignees: Iterable[str] = (),
    ) -> TeamTask:
        current = self.tasks.get(task_id)
        if current is None:
            raise TeamError("unknown_team_task", "Team task does not exist.")
        if current.terminal:
            raise TeamError(
                "terminal_team_task", "A completed or cancelled Team task is immutable."
            )
        new_assignee = current.assignee if assignee is UNSET else assignee
        if new_assignee == "":
            new_assignee = None
        new_status = status or current.status
        entering_progress = (
            current.status is not TeamTaskStatus.IN_PROGRESS
            and new_status is TeamTaskStatus.IN_PROGRESS
        )
        self._validate_assignee(
            new_assignee, valid_assignees, inactive_assignees, entering_progress=entering_progress
        )
        dependencies = (current.blocked_by | frozenset(add_blocked_by)) - frozenset(
            remove_blocked_by
        )
        if task_id in dependencies:
            raise TeamError("task_dependency_cycle", "A Team task cannot depend on itself.")
        if any(item not in self.tasks for item in dependencies):
            raise TeamError("unknown_team_task", "A Team task dependency does not exist.")
        allowed = {
            TeamTaskStatus.PENDING: {
                TeamTaskStatus.PENDING,
                TeamTaskStatus.IN_PROGRESS,
                TeamTaskStatus.COMPLETED,
                TeamTaskStatus.CANCELLED,
            },
            TeamTaskStatus.IN_PROGRESS: {
                TeamTaskStatus.IN_PROGRESS,
                TeamTaskStatus.COMPLETED,
                TeamTaskStatus.CANCELLED,
            },
        }
        if new_status not in allowed[current.status]:
            raise TeamError("invalid_task_transition", "Invalid Team task status transition.")
        candidate = replace(
            current,
            status=new_status,
            assignee=new_assignee,
            blocked_by=frozenset(dependencies),
            updated_at=time.time(),
        )
        snapshot = dict(self.tasks)
        snapshot[task_id] = candidate
        self._assert_acyclic(snapshot)
        if new_status is TeamTaskStatus.IN_PROGRESS and not all(
            snapshot[item].status is TeamTaskStatus.COMPLETED for item in dependencies
        ):
            raise TeamError("task_blocked", "Team task dependencies are not completed.")
        self.tasks[task_id] = candidate
        return candidate

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from kcode.config import SubAgentConfig, TeamConfig
from kcode.permissions.models import PermissionMode
from kcode.subagents.catalog import AgentCatalog
from kcode.subagents.factory import SubAgentFactory
from kcode.subagents.manager import TaskFinalization, TaskManager, TaskRecord
from kcode.subagents.models import TaskKind, TaskStatus
from kcode.teams.mailbox import TeamMailbox
from kcode.teams.models import (
    IsolationMode,
    Team,
    TeamCaller,
    TeamError,
    TeamMember,
    TeamMemberStatus,
    TeamOperationResult,
    TeamTask,
    TeamTaskStatus,
    validate_team_slug,
)
from kcode.teams.rendering import protected_result, redact
from kcode.teams.task_board import UNSET, TaskBoard
from kcode.teams.tools import member_tools
from kcode.tools.base import JSONValue
from kcode.worktrees import WorktreeError, WorktreeManager, WorktreeRecord, WorktreeStatus

TERMINAL_MEMBER_STATUSES = {TeamMemberStatus.STOPPED, TeamMemberStatus.FAILED}
ACTIVE_MEMBER_STATUSES = {
    TeamMemberStatus.STARTING,
    TeamMemberStatus.RUNNING,
    TeamMemberStatus.STOPPING,
}


class TeamManager:
    def __init__(
        self,
        config: TeamConfig,
        subagent_config: SubAgentConfig,
        catalog: AgentCatalog,
        factory: SubAgentFactory,
        task_manager: TaskManager,
        parent,
        worktrees: WorktreeManager | None,
        *,
        sensitive_values: tuple[str, ...] = (),
    ) -> None:
        self.config = config
        self.subagent_config = subagent_config
        self.catalog = catalog
        self.factory = factory
        self.task_manager = task_manager
        self.parent = parent
        self.worktrees = worktrees
        self.sensitive_values = sensitive_values
        self.active: Team | None = None
        self.mailbox = TeamMailbox(sensitive_values)
        self.mailbox.register("lead")
        self._lock = asyncio.Lock()
        self._closed = False

    def set_catalog(self, catalog: AgentCatalog) -> None:
        self.catalog = catalog

    def lead_message_source(self):
        return self.mailbox.source("lead")

    def _enabled(self) -> None:
        if not self.config.enabled:
            raise TeamError("teams_disabled", "Agent Teams are disabled in user configuration.")
        if self._closed:
            raise TeamError("team_manager_closed", "Agent Team coordination is closed.")

    def _team(self) -> Team:
        self._enabled()
        if self.active is None:
            raise TeamError("no_active_team", "No Agent Team is active.")
        return self.active

    def _caller(self, caller: TeamCaller, *, lifecycle: bool = False) -> tuple[Team, str]:
        team = self._team()
        if caller.role == "lead":
            return team, "lead"
        if lifecycle:
            raise TeamError(
                "team_permission_denied", "A Team member cannot control Team lifecycle."
            )
        if caller.team_id != team.id or caller.member_name not in team.members:
            raise TeamError("invalid_team_caller", "Team caller identity is stale or invalid.")
        member = team.members[caller.member_name]
        if member.status in TERMINAL_MEMBER_STATUSES:
            raise TeamError("member_not_resumable", "Stopped or failed Team member is inactive.")
        return team, member.name

    @staticmethod
    def _worktree_data(status: WorktreeStatus) -> dict[str, JSONValue]:
        return {
            "path": str(status.path),
            "branch": status.branch,
            "head_commit": status.head_commit,
            "dirty": status.dirty,
            "head_changed": status.head_changed,
            "managed": status.managed,
            "removable": status.removable,
            "warnings": list(status.warnings),
        }

    @staticmethod
    def _task_data(task: TeamTask, board: TaskBoard) -> dict[str, JSONValue]:
        incomplete = [
            item
            for item in sorted(task.blocked_by)
            if board.tasks[item].status is not TeamTaskStatus.COMPLETED
        ]
        return {
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status.value,
            "assignee": task.assignee,
            "blocked_by": sorted(task.blocked_by),
            "ready": not incomplete,
            "incomplete_dependencies": incomplete,
            "created_by": task.created_by,
        }

    async def create(self, caller: TeamCaller, name: str, goal: str) -> TeamOperationResult:
        self._enabled()
        if caller.role != "lead":
            raise TeamError("team_permission_denied", "Only Lead can create a Team.")
        name = validate_team_slug(name)
        if not goal or not goal.strip():
            raise TeamError("invalid_team_goal", "Team goal must not be empty.")
        async with self._lock:
            if self.active is not None:
                raise TeamError("team_exists", "Only one Agent Team may be active.")
            self.mailbox.clear()
            self.mailbox.register("lead")
            self.active = Team(name, redact(goal, self.sensitive_values))
            return TeamOperationResult(
                {"team_id": self.active.id, "name": name, "goal": self.active.goal}
            )

    def _mode(self) -> PermissionMode:
        snapshot = self.parent.delegation_snapshot
        return snapshot.mode if snapshot is not None else PermissionMode.DEFAULT

    async def spawn(
        self,
        caller: TeamCaller,
        name: str,
        prompt: str,
        subagent_type: str = "general-purpose",
        isolation: str = "worktree",
    ) -> TeamOperationResult:
        team, _ = self._caller(caller, lifecycle=True)
        name = validate_team_slug(name)
        try:
            isolation_mode = IsolationMode(isolation)
        except ValueError as exc:
            raise TeamError("invalid_isolation", "Isolation must be shared or worktree.") from exc
        if not self.subagent_config.enabled:
            raise TeamError("subagents_disabled", "SubAgents are disabled.")
        if not self.subagent_config.background_enabled:
            raise TeamError("background_disabled", "Background SubAgents are disabled.")
        definition, catalog_warnings = self.catalog.resolve(subagent_type)
        if definition is None:
            raise TeamError("unknown_subagent", f"Unknown or unavailable Agent: {subagent_type}")
        task_id = self.task_manager.make_task_id()
        async with self._lock:
            if self.active is not team:
                raise TeamError("no_active_team", "No Agent Team is active.")
            if name in team.members:
                raise TeamError("member_exists", "Team member name is already used.")
            if len(team.members) >= self.config.max_members:
                raise TeamError("team_member_limit", "Team member limit reached.")
            if not self.task_manager.can_launch():
                raise TeamError("subagent_capacity", "SubAgent running or retained limit reached.")
            member = TeamMember(name, task_id, subagent_type, isolation_mode)
            team.members[name] = member
            self.mailbox.register(name)
        worktree: WorktreeRecord | None = None
        try:
            context = self.parent.context
            worktree_notice = ""
            warnings = list(catalog_warnings)
            if isolation_mode is IsolationMode.WORKTREE:
                if self.worktrees is None:
                    raise TeamError("worktrees_unavailable", "Worktree isolation is unavailable.")
                worktree = await self.worktrees.create_agent(task_id)
                context = replace(
                    self.parent.context,
                    workspace_root=worktree.path,
                    cancel_event=None,
                    use_shell=False,
                )
                worktree_notice = self._worktree_notice(worktree)
            else:
                warnings.append(
                    "Team member uses shared isolation; concurrent writes may conflict."
                )
            caller_binding = TeamCaller.member(name, team.id)
            child = self.factory.team_member(
                definition,
                self.parent,
                self._mode(),
                self.parent.approve,
                context=context,
                collaboration_tools=member_tools(self, caller_binding),
                message_source=self.mailbox.source(name),
                team_notice=self._team_notice(team, member),
                worktree_notice=worktree_notice,
            )

            async def finalize(_record: TaskRecord) -> TaskFinalization:
                if worktree is None or self.worktrees is None:
                    return TaskFinalization()
                report = await self.worktrees.finalize(worktree, task_id)
                return TaskFinalization(report.render(), report.warnings, report.to_dict())

            async def complete(record: TaskRecord) -> None:
                await self._member_completed(team.id, name, task_id, record)

            async with self._lock:
                current = team.members[name]
                current.worktree = worktree
                current.status = TeamMemberStatus.RUNNING
                current.updated_at = time.time()
            await self.task_manager.launch(
                child,
                prompt,
                f"{team.name}/{name}",
                background=True,
                task_id=task_id,
                finalizer=finalize if worktree is not None else None,
                kind=TaskKind.TEAM_MEMBER,
                retain_on_success=True,
                pinned=True,
                completion_callback=complete,
            )
            return TeamOperationResult(
                {
                    "name": name,
                    "task_id": task_id,
                    "status": "running",
                    "isolation": isolation_mode.value,
                    "worktree": str(worktree.path) if worktree is not None else None,
                },
                tuple(warnings),
            )
        except asyncio.CancelledError:
            await self._rollback_spawn(team, name, task_id, worktree)
            raise
        except (TeamError, WorktreeError, RuntimeError, ValueError, KeyError) as exc:
            report = await self._rollback_spawn(team, name, task_id, worktree)
            if isinstance(exc, TeamError):
                raise
            code = exc.code if isinstance(exc, WorktreeError) else "team_spawn_failed"
            message = str(exc)
            if report:
                message += "\n\n" + report
            raise TeamError(code, message) from exc

    async def _rollback_spawn(
        self,
        team: Team,
        name: str,
        task_id: str,
        worktree: WorktreeRecord | None,
    ) -> str:
        async with self._lock:
            if self.active is team:
                team.members.pop(name, None)
        if worktree is None or self.worktrees is None:
            return ""
        return (await self.worktrees.finalize(worktree, task_id)).render()

    async def _member_completed(
        self,
        team_id: str,
        name: str,
        task_id: str,
        record: TaskRecord,
    ) -> None:
        should_resume = False
        async with self._lock:
            team = self.active
            if team is None or team.id != team_id:
                return
            member = team.members.get(name)
            if member is None or member.task_id != task_id:
                return
            member.total_tokens = record.usage.total_tokens
            member.last_result = record.result
            member.last_error = record.error
            member.worktree_report = dict(record.finalization_details) or None
            if member.status is TeamMemberStatus.STOPPING:
                member.status = TeamMemberStatus.STOPPED
            elif record.status is TaskStatus.COMPLETED:
                member.status = TeamMemberStatus.IDLE
                should_resume = self.mailbox.pending(name) > 0 and not self._closed
                member.wake_scheduled = should_resume
            else:
                member.status = TeamMemberStatus.FAILED
            member.updated_at = time.time()
            report = await self._member_report(member, record)
            self.mailbox.deliver(name, ("lead",), report)
        if should_resume:
            asyncio.create_task(self._resume_after_completion(team_id, name))

    async def _member_report(self, member: TeamMember, record: TaskRecord) -> str:
        tail = (
            f"Member: {member.name}\nStatus: {member.status.value}\n"
            f"Isolation: {member.isolation.value}\nToken: {record.usage.total_tokens}"
        )
        if member.worktree_report:
            tail += "\nWorktree: " + str(member.worktree_report)
        elif member.worktree is not None and self.worktrees is not None:
            try:
                status = await self.worktrees.status(member.worktree.name)
                tail += "\nWorktree: " + str(self._worktree_data(status))
            except WorktreeError:
                tail += f"\nWorktree: kept for review at {member.worktree.path}"
        rendered, _ = protected_result(record.result or record.error, tail, self.sensitive_values)
        return rendered

    async def _resume_after_completion(self, team_id: str, name: str) -> None:
        while True:
            team = self.active
            member = team.members.get(name) if team is not None and team.id == team_id else None
            if member is None:
                return
            record = self.task_manager.get(member.task_id, TaskKind.TEAM_MEMBER)
            if record is None or record.task is None or record.task.done():
                break
            await asyncio.sleep(0)
        try:
            await self._resume_member(team_id, name)
        except TeamError:
            return

    async def _resume_member(self, team_id: str, name: str) -> None:
        async with self._lock:
            team = self.active
            if self._closed or team is None or team.id != team_id:
                raise TeamError("member_not_resumable", "Team member cannot be resumed.")
            member = team.members.get(name)
            if member is None or member.status is not TeamMemberStatus.IDLE:
                if member is not None:
                    member.wake_scheduled = False
                return
            member.status = TeamMemberStatus.RUNNING
            member.wake_scheduled = True
        try:
            await self.task_manager.resume_retained(
                member.task_id,
                "Read the pending <team-messages> reminder and continue Team collaboration.",
                expected_kind=TaskKind.TEAM_MEMBER,
            )
        except (RuntimeError, ValueError) as exc:
            async with self._lock:
                member.status = TeamMemberStatus.IDLE
                member.wake_scheduled = False
            raise TeamError("member_resume_failed", str(exc)) from exc

    async def status(self, caller: TeamCaller) -> TeamOperationResult:
        team, _ = self._caller(caller)
        board = TaskBoard(team.tasks)
        members: list[JSONValue] = []
        for item in team.members.values():
            worktree: JSONValue = item.worktree_report
            if worktree is None and item.worktree is not None and self.worktrees is not None:
                try:
                    worktree = self._worktree_data(await self.worktrees.status(item.worktree.name))
                except WorktreeError:
                    worktree = {"path": str(item.worktree.path), "status": "unknown_kept"}
            members.append(
                {
                    "name": item.name,
                    "status": item.status.value,
                    "isolation": item.isolation.value,
                    "tokens": item.total_tokens,
                    "worktree": worktree,
                    "pending_messages": self.mailbox.pending(item.name),
                }
            )
        counts = {status.value: len(board.list(status)) for status in TeamTaskStatus}
        return TeamOperationResult(
            {
                "enabled": True,
                "name": team.name,
                "goal": team.goal,
                "members": members,
                "tasks": counts,
                "lead_pending_messages": self.mailbox.pending("lead"),
            }
        )

    async def send_message(self, caller: TeamCaller, to: str, message: str) -> TeamOperationResult:
        team, sender = self._caller(caller)
        awakened: list[str] = []
        async with self._lock:
            if to == "*":
                recipients = tuple(item for item in ("lead", *team.members) if item != sender)
            else:
                recipients = (to,)
            for recipient in recipients:
                if recipient != "lead":
                    member = team.members.get(recipient)
                    if member is None:
                        raise TeamError("unknown_member", "Team message recipient does not exist.")
                    if member.status in TERMINAL_MEMBER_STATUSES:
                        raise TeamError(
                            "member_not_resumable",
                            "Stopped or failed member cannot receive messages.",
                        )
            self.mailbox.deliver(sender, recipients, message)
            for recipient in recipients:
                member = team.members.get(recipient)
                if member is not None and member.status is TeamMemberStatus.IDLE:
                    if not member.wake_scheduled:
                        member.wake_scheduled = True
                        awakened.append(recipient)
        for recipient in awakened:
            await self._resume_member(team.id, recipient)
        return TeamOperationResult({"recipients": list(recipients), "awakened": awakened})

    def _board_context(self, team: Team) -> tuple[TaskBoard, tuple[str, ...], tuple[str, ...]]:
        active = tuple(team.members)
        inactive = tuple(
            item.name for item in team.members.values() if item.status in TERMINAL_MEMBER_STATUSES
        )
        return TaskBoard(team.tasks), active, inactive

    async def task_create(
        self,
        caller: TeamCaller,
        title: str,
        description: str,
        assignee: str | None = None,
        blocked_by: tuple[str, ...] = (),
    ) -> TeamOperationResult:
        team, sender = self._caller(caller)
        async with self._lock:
            board, active, _inactive = self._board_context(team)
            task = board.create(
                title=title,
                description=description,
                assignee=assignee,
                blocked_by=blocked_by,
                created_by=sender,
                valid_assignees=active,
            )
            return TeamOperationResult(self._task_data(task, board))

    async def task_list(
        self, caller: TeamCaller, status: TeamTaskStatus | None = None
    ) -> TeamOperationResult:
        team, _ = self._caller(caller)
        board = TaskBoard(team.tasks)
        return TeamOperationResult(
            {"tasks": [self._task_data(item, board) for item in board.list(status)]}
        )

    async def task_update(
        self,
        caller: TeamCaller,
        task_id: str,
        status: TeamTaskStatus | None = None,
        assignee: str | None = None,
        add_blocked_by: tuple[str, ...] = (),
        remove_blocked_by: tuple[str, ...] = (),
    ) -> TeamOperationResult:
        team, _ = self._caller(caller)
        async with self._lock:
            board, active, inactive = self._board_context(team)
            value = UNSET if assignee is None else assignee
            task = board.update(
                task_id,
                status=status,
                assignee=value,
                add_blocked_by=add_blocked_by,
                remove_blocked_by=remove_blocked_by,
                valid_assignees=active,
                inactive_assignees=inactive,
            )
            return TeamOperationResult(self._task_data(task, board))

    async def stop(self, caller: TeamCaller, name: str) -> TeamOperationResult:
        team, _ = self._caller(caller, lifecycle=True)
        name = validate_team_slug(name)
        async with self._lock:
            member = team.members.get(name)
            if member is None:
                raise TeamError("unknown_member", "Team member does not exist.")
            if member.status in TERMINAL_MEMBER_STATUSES:
                return TeamOperationResult({"name": name, "status": member.status.value})
            was_idle = member.status is TeamMemberStatus.IDLE
            member.status = TeamMemberStatus.STOPPING
        if not was_idle:
            self.task_manager.stop(member.task_id, TaskKind.TEAM_MEMBER)
            await self.task_manager.wait(member.task_id, 5.0, expected_kind=TaskKind.TEAM_MEMBER)
        record = self.task_manager.get(member.task_id, TaskKind.TEAM_MEMBER)
        if record is not None and record.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            record = await self.task_manager.finalize_retained(
                member.task_id, expected_kind=TaskKind.TEAM_MEMBER
            )
            member.worktree_report = dict(record.finalization_details) or None
            member.total_tokens = record.usage.total_tokens
            if record.task is None or record.task.done():
                self.task_manager.release(member.task_id, expected_kind=TaskKind.TEAM_MEMBER)
            member.status = TeamMemberStatus.STOPPED
        return TeamOperationResult(
            {
                "name": name,
                "status": member.status.value,
                "worktree": member.worktree_report,
            }
        )

    async def delete(self, caller: TeamCaller) -> TeamOperationResult:
        team, _ = self._caller(caller, lifecycle=True)
        async with self._lock:
            if any(item.status in ACTIVE_MEMBER_STATUSES for item in team.members.values()):
                raise TeamError("team_running", "Stop all running Team members before delete.")
        reports: list[JSONValue] = []
        for member in tuple(team.members.values()):
            if member.status is TeamMemberStatus.IDLE:
                await self.stop(caller, member.name)
            if member.worktree_report:
                reports.append(member.worktree_report)
            self.task_manager.release(member.task_id, expected_kind=TaskKind.TEAM_MEMBER)
        async with self._lock:
            if self.active is team:
                self.active = None
                self.mailbox.clear()
                self.mailbox.register("lead")
        return TeamOperationResult({"deleted": True, "worktrees": reports})

    async def close(self) -> tuple[str, ...]:
        if self._closed:
            return ()
        self._closed = True
        team = self.active
        if team is None:
            return ()
        members = tuple(team.members.values())
        for member in members:
            if member.status in {TeamMemberStatus.STARTING, TeamMemberStatus.RUNNING}:
                member.status = TeamMemberStatus.STOPPING
                self.task_manager.stop(member.task_id, TaskKind.TEAM_MEMBER)
        await asyncio.gather(
            *(
                self.task_manager.wait(item.task_id, 5.0, expected_kind=TaskKind.TEAM_MEMBER)
                for item in members
            ),
            return_exceptions=True,
        )
        warnings: list[str] = []
        for member in members:
            record = self.task_manager.get(member.task_id, TaskKind.TEAM_MEMBER)
            if record is None or record.status not in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                warnings.append(f"Team member {member.name} did not finish before shutdown.")
                continue
            await self.task_manager.finalize_retained(
                member.task_id, expected_kind=TaskKind.TEAM_MEMBER
            )
            self.task_manager.release(member.task_id, expected_kind=TaskKind.TEAM_MEMBER)
        self.active = None
        self.mailbox.clear()
        return tuple(warnings)

    def _team_notice(self, team: Team, member: TeamMember) -> str:
        return (
            "<team-context>\n"
            f"Team: {team.name}\nGoal: {team.goal}\nMember: {member.name}\n"
            f"Isolation: {member.isolation.value}\n"
            "Use Team messages and the shared task board to coordinate. You cannot create, stop, "
            "or delete Agents or Teams.\n</team-context>"
        )

    def _worktree_notice(self, record: WorktreeRecord) -> str:
        return (
            "<worktree-context>\nYou are running in an isolated Git Worktree.\n"
            f"Parent workspace: {self.parent.context.workspace_root}\n"
            f"Your workspace: {record.path}\nYour branch: {record.branch}\n"
            "Re-read local files and do not access the parent workspace or another Worktree.\n"
            "</worktree-context>"
        )

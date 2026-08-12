from pathlib import Path

from kcode.teams import TeamCaller, TeamError, TeamOperationResult
from kcode.teams.tools import lead_tools, member_tools, register_team_tools
from kcode.tools.base import ToolContext, ToolEffect
from kcode.tools.registry import ToolRegistry


class Manager:
    def __init__(self, error: TeamError | None = None) -> None:
        self.error = error
        self.calls = []

    def __getattr__(self, name):
        async def call(caller, **values):
            self.calls.append((name, caller, values))
            if self.error is not None:
                raise self.error
            return TeamOperationResult({"method": name})

        return call


def test_lead_and_member_tool_sets_are_stable() -> None:
    manager = Manager()
    lead = lead_tools(manager)
    member = member_tools(manager, TeamCaller.member("alice", "team-000000000001"))
    assert len(lead) == 9
    assert {item.spec.name for item in member} == {
        "team_send_message",
        "team_task_create",
        "team_task_list",
        "team_task_update",
    }
    assert {item.spec.name for item in lead if item.spec.effect is ToolEffect.READ_ONLY} == {
        "team_status",
        "team_task_list",
    }
    for tool in lead:
        assert "sender" not in tool.spec.arguments_model.model_fields


async def test_tool_uses_bound_caller_and_maps_error() -> None:
    manager = Manager()
    tool = next(item for item in lead_tools(manager) if item.spec.name == "team_send_message")
    result = await tool.execute(
        tool.spec.arguments_model(to="alice", message="hello"), ToolContext(Path.cwd())
    )
    assert result.status == "success"
    assert manager.calls[0][1] == TeamCaller.lead()

    disabled = Manager(TeamError("teams_disabled", "disabled"))
    tool = lead_tools(disabled)[0]
    result = await tool.execute(
        tool.spec.arguments_model(name="core", goal="ship"), ToolContext(Path.cwd())
    )
    assert result.error.code == "teams_disabled"


async def test_all_disabled_tools_return_same_stable_error() -> None:
    disabled = Manager(TeamError("teams_disabled", "disabled"))
    context = ToolContext(Path.cwd())
    for tool in lead_tools(disabled):
        values = {
            "team_create": {"name": "core", "goal": "ship"},
            "team_spawn": {"name": "alice", "prompt": "work"},
            "team_stop": {"name": "alice"},
            "team_send_message": {"to": "lead", "message": "hello"},
            "team_task_create": {"title": "task", "description": "work"},
            "team_task_update": {"task_id": "team-task-000000000000"},
        }.get(tool.spec.name, {})
        result = await tool.execute(tool.spec.arguments_model(**values), context)
        assert result.error.code == "teams_disabled"


def test_registration_schema_does_not_depend_on_state() -> None:
    first = ToolRegistry()
    second = ToolRegistry()
    register_team_tools(first, Manager())
    register_team_tools(second, Manager(TeamError("teams_disabled", "disabled")))
    assert first.names() == second.names()
    assert first.definitions() == second.definitions()


async def test_tool_result_is_bounded() -> None:
    class Large(Manager):
        async def status(self, caller):
            return TeamOperationResult({"value": "x" * (40 * 1024)})

    tool = next(item for item in lead_tools(Large()) if item.spec.name == "team_status")
    result = await tool.execute(tool.spec.arguments_model(), ToolContext(Path.cwd()))
    assert result.truncated
    assert result.data["truncated"] is True
    assert len(result.to_json().encode("utf-8")) <= 33 * 1024

from pathlib import Path

from kcode.subagents.tools import (
    AgentArgs,
    AgentTool,
    EmptyArgs,
    TaskGetTool,
    TaskIdArgs,
    TaskListTool,
    TaskSendMessageArgs,
    TaskSendMessageTool,
    TaskStopTool,
    register_subagent_tools,
)
from kcode.tools.base import ToolContext, ToolResult
from kcode.tools.registry import ToolRegistry


class Service:
    async def invoke(self, **values):
        return ToolResult.success(values)

    def list_tasks(self):
        return ToolResult.success({"tasks": []})

    def get_task(self, task_id):
        return ToolResult.success({"task_id": task_id})

    def stop_task(self, task_id):
        return ToolResult.success({"task_id": task_id})

    async def send_message(self, task_id, message):
        return ToolResult.success({"task_id": task_id, "message": message})


async def test_five_stable_tools_register_and_dispatch() -> None:
    service = Service()
    registry = ToolRegistry()
    register_subagent_tools(registry, service)
    assert registry.names() == {
        "agent",
        "task_list",
        "task_get",
        "task_stop",
        "task_send_message",
    }
    context = ToolContext(Path.cwd())
    result = await AgentTool(service).execute(
        AgentArgs(prompt="inspect", description="Explore", subagent_type="explore"),
        context,
    )
    assert result.data["subagent_type"] == "explore"
    assert (await TaskListTool(service).execute(EmptyArgs(), context)).status == "success"
    task_id = "task-123456789abc"
    assert (await TaskGetTool(service).execute(TaskIdArgs(task_id=task_id), context)).data[
        "task_id"
    ] == task_id
    assert (
        await TaskStopTool(service).execute(TaskIdArgs(task_id=task_id), context)
    ).status == "success"
    sent = await TaskSendMessageTool(service).execute(
        TaskSendMessageArgs(task_id=task_id, message="continue"),
        context,
    )
    assert sent.data["message"] == "continue"


def test_agent_schema_is_catalog_independent_and_self_managed() -> None:
    schema = AgentTool(Service()).spec.arguments_model.model_json_schema()
    assert "subagent_type" in schema["properties"]
    assert "explore" not in str(schema)
    assert AgentTool(Service()).spec.self_managed_timeout

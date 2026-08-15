from pathlib import Path

from textual.widgets import Input, Static

from kcode.subagents.trust import AgentTrustStore
from kcode.ui.agent_trust import AgentTrustScreen
from kcode.ui.app import KCodeApp


class FakeProvider:
    display_name = "fake-provider"
    model_name = "fake-model"

    async def stream(self, _messages, _tools=(), tool_choice="auto"):
        if False:
            yield


def write_agent(project: Path, body: str) -> None:
    path = project / ".kcode" / "agents" / "project-review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "name: project-review\n"
        "description: Review this project\n"
        "tools: [read_file]\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


async def test_agent_trust_blocks_input_and_rejection_keeps_builtins(tmp_path: Path) -> None:
    write_agent(tmp_path, "PRIVATE ROLE BODY")
    app = KCodeApp(
        FakeProvider(),
        cwd=tmp_path,
        agent_trust_store=AgentTrustStore(tmp_path / "trust.json"),
    )
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AgentTrustScreen)
        assert app.query_one("#prompt", Input).disabled
        summary = str(app.screen.query_one("#agent-trust-summary", Static).content)
        assert "project-review" in summary
        assert "PRIVATE ROLE BODY" not in summary
        await pilot.press("2")
        await pilot.pause()
        assert not app.query_one("#prompt", Input).disabled
        assert app.command_registry.frozen
        assert app.subagent_service.catalog.get("project-review") is None
        assert {item.name for item in app.subagent_service.catalog.summaries()} == {
            "explore",
            "general-purpose",
            "plan",
        }


async def test_startup_registers_stable_tools_and_available_agents(tmp_path: Path) -> None:
    app = KCodeApp(FakeProvider(), cwd=tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        assert {
            "agent",
            "task_list",
            "task_get",
            "task_stop",
            "task_send_message",
        } <= app.registry.names()
        prompt = app.runner.prompt_builder.build()
        assert "## Available Agents" in prompt
        assert "general-purpose" in prompt
        assert "focused Kcode sub-agent" not in prompt

from pathlib import Path

from textual.widgets import Input, Static

from kcode.hooks.catalog import HookCatalogBuilder
from kcode.mcp.manager import McpStartupSummary
from kcode.mcp.trust import McpTrustRequest
from kcode.ui.app import KCodeApp
from kcode.ui.mcp_trust import McpTrustScreen


class FakeProvider:
    display_name = "fake-provider"
    model_name = "fake-model"

    async def stream(self, _messages):
        if False:
            yield


class TrustStore:
    def __init__(self) -> None:
        self.cleared = False

    def clear_project(self, _root: Path) -> bool:
        self.cleared = True
        return True


class FakeManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.trust_store = TrustStore()
        self.approved = None
        self.closed = False

    async def prepare(self, trust):
        self.approved = await trust(
            McpTrustRequest(
                self.root,
                "local",
                "stdio",
                "python server.py",
                ("SECRET_TOKEN",),
                "fingerprint",
            )
        )

    async def connect_all(self):
        return McpStartupSummary(
            (),
            (),
            ("local",) if self.approved is False else (),
            (),
            ("KCode skipped untrusted MCP server 'local'.",) if self.approved is False else (),
            (),
        )

    async def close(self) -> None:
        self.closed = True


class FailingManager(FakeManager):
    async def prepare(self, trust):
        raise RuntimeError("connection setup failed")


async def test_project_trust_is_shown_before_ready_without_secret_value(
    tmp_path: Path,
) -> None:
    manager = FakeManager(tmp_path)
    app = KCodeApp(FakeProvider(), cwd=tmp_path, mcp_manager=manager)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, McpTrustScreen)
        summary = str(app.screen.query_one("#mcp-trust-summary", Static).content)
        assert "SECRET_TOKEN" in summary
        assert "actual-secret-value" not in summary
        assert app.query_one("#prompt", Input).disabled
        await pilot.press("2")
        await pilot.pause()
        assert manager.approved is False
        assert not app.query_one("#prompt", Input).disabled
        assert "1 skipped" in str(app.query_one("#ready", Static).content)
    assert manager.closed


async def test_mcp_trust_clear_command_uses_current_project(tmp_path: Path) -> None:
    manager = FakeManager(tmp_path)
    app = KCodeApp(FakeProvider(), cwd=tmp_path, mcp_manager=manager)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/mcp-trust-clear"
        await pilot.press("enter")
        await pilot.pause()
        assert manager.trust_store.cleared


async def test_mcp_failure_does_not_skip_hook_finalization(tmp_path: Path) -> None:
    user_hooks = tmp_path / "user-hooks.yaml"
    user_hooks.write_text(
        "hooks:\n"
        "  - id: mcp-fallback\n"
        "    event: startup\n"
        "    action: {type: prompt, message: continue without MCP}\n",
        encoding="utf-8",
    )
    manager = FailingManager(tmp_path)
    builder = HookCatalogBuilder(tmp_path, user_path=user_hooks)
    app = KCodeApp(
        FakeProvider(),
        cwd=tmp_path,
        mcp_manager=manager,
        hook_builder=builder,
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        assert not app.query_one("#prompt", Input).disabled
        assert app.command_registry.frozen
        assert [item.id for item in app.command_hooks()] == ["mcp-fallback"]
        assert "Skills/Hooks are available" in str(app.query_one("#ready", Static).content)

import asyncio
import json

import pytest
from textual.widgets import Collapsible, Input, Markdown, Static

from kcode.conversation import Conversation
from kcode.errors import ProviderError, ProviderErrorKind
from kcode.events import StreamCompleted, TextDelta, ThinkingDelta, ToolCallDelta
from kcode.ui.app import KCodeApp
from kcode.ui.approval import ApprovalScreen
from kcode.ui.widgets import AssistantResponse, ChatMessageWidget, ToolCallWidget


class FakeProvider:
    display_name = "fake-provider"
    model_name = "fake-model"

    def __init__(self, events=None, error=None, delay=0):
        self.events = events or []
        self.error = error
        self.delay = delay
        self.requests = []
        self.closed = False

    async def stream(self, messages):
        self.requests.append(tuple(messages))
        try:
            for event in self.events:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield event
            if self.error:
                raise self.error
        finally:
            self.closed = True


async def submit(app: KCodeApp, pilot, text: str) -> None:
    prompt = app.query_one("#prompt", Input)
    prompt.value = text
    await pilot.press("enter")


async def test_ac7_fixed_layout_at_80_by_24() -> None:
    app = KCodeApp(FakeProvider(), cwd=None)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert "KCode v0.2.0" in str(app.query_one("#banner", Static).content)
        assert app.query_one("#ready").render().plain == "Ready. Ask me anything."
        assert app.query_one("#prompt-marker", Static).content == "❯"
        assert app.query_one("#prompt", Input).placeholder == "Send a message..."
        assert "fake-provider" in str(app.query_one("#provider-status", Static).content)
        assert "fake-model" in str(app.query_one("#model-status", Static).content)
        assert app.query_one("#chat").region.height > 0


async def test_stream_commits_history_and_folds_thinking() -> None:
    provider = FakeProvider(
        [ThinkingDelta("why"), TextDelta("hello"), TextDelta(" world"), StreamCompleted("stop")],
        delay=0.01,
    )
    conversation = Conversation()
    app = KCodeApp(provider, conversation)
    async with app.run_test(size=(80, 24)) as pilot:
        await submit(app, pilot, "first")
        await pilot.pause(0.15)
        response = app.query_one(AssistantResponse)
        assert response.answer_text == "hello world"
        assert response.thinking_text == "why"
        assert response.query_one(Collapsible).collapsed is True
        assert conversation.snapshot()[0].assistant == "hello world"
        await submit(app, pilot, "second")
        await pilot.pause(0.15)
        assert len(provider.requests[1]) == 3


async def test_ctrl_c_cancels_partial_answer_without_history() -> None:
    provider = FakeProvider([TextDelta("partial"), TextDelta("later"), StreamCompleted()], delay=0.2)
    conversation = Conversation()
    app = KCodeApp(provider, conversation)
    async with app.run_test(size=(80, 24)) as pilot:
        await submit(app, pilot, "cancel me")
        await pilot.pause(0.25)
        assert app.query_one(AssistantResponse).answer_text == "partial"
        await pilot.press("ctrl+c")
        await pilot.pause(0.05)
        assert conversation.snapshot() == ()
        assert provider.closed is True
        assert app.query_one("#prompt", Input).disabled is False
        assert "Cancelled" in app.query_one(AssistantResponse).answer_text
        provider.events = [TextDelta("next works"), StreamCompleted("stop")]
        provider.delay = 0
        await submit(app, pilot, "next")
        await pilot.pause(0.05)
        assert conversation.snapshot()[0].assistant == "next works"


@pytest.mark.parametrize("kind", list(ProviderErrorKind))
async def test_provider_errors_do_not_commit_and_input_recovers(kind: ProviderErrorKind) -> None:
    provider = FakeProvider(error=ProviderError(kind, "safe error"))
    conversation = Conversation()
    app = KCodeApp(provider, conversation)
    async with app.run_test(size=(80, 24)) as pilot:
        await submit(app, pilot, "fail")
        await pilot.pause(0.05)
        assert conversation.snapshot() == ()
        assert app.query_one("#prompt", Input).disabled is False
        assert kind.value in app.query_one(AssistantResponse).answer_text


async def test_commands_are_local_and_clear_history() -> None:
    provider = FakeProvider()
    conversation = Conversation()
    conversation.commit("old", "answer")
    app = KCodeApp(provider, conversation)
    async with app.run_test() as pilot:
        await submit(app, pilot, "/help")
        await pilot.pause()
        await submit(app, pilot, "/clear")
        await pilot.pause()
        assert provider.requests == []
        assert conversation.snapshot() == ()


async def test_openai_thinking_warning_is_shown_without_a_request() -> None:
    provider = FakeProvider()
    app = KCodeApp(provider, warnings=("thinking is ignored",))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        notices = list(app.query(ChatMessageWidget))
        assert notices[0].text == "thinking is ignored"
        assert provider.requests == []


async def test_markdown_and_fenced_code_are_sent_to_markdown_renderer() -> None:
    source = "**bold**\n\n```python\nprint('highlighted')\n```"
    provider = FakeProvider([TextDelta(source), StreamCompleted("stop")])
    app = KCodeApp(provider)
    async with app.run_test(size=(80, 24)) as pilot:
        await submit(app, pilot, "render")
        await pilot.pause(0.05)
        assert app.query_one("#answer-content", Markdown).source == source


class ToolCallingProvider:
    display_name = "tool-provider"
    model_name = "tool-model"

    def __init__(self, target):
        self.target = target
        self.calls = 0

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.calls += 1
        if self.calls == 1:
            yield TextDelta("内部 DSML 工具标记")
            yield ToolCallDelta(
                0,
                "write-1",
                "write_file",
                '{"path":"%s","content":"blocked"}' % self.target,
            )
            yield StreamCompleted("tool_calls")
        else:
            yield TextDelta("写入已被用户拒绝。")
            yield StreamCompleted("stop")


async def test_external_write_approval_can_be_denied(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    app = KCodeApp(ToolCallingProvider(outside), cwd=workspace)
    async with app.run_test(size=(100, 30)) as pilot:
        await submit(app, pilot, "在外部写文件")
        await pilot.pause(0.1)
        assert isinstance(app.screen, ApprovalScreen)
        await pilot.click("#deny")
        await pilot.pause(0.15)
        assert not outside.exists()
        assert app.query_one(ToolCallWidget).query_one(".tool-status").render().plain.startswith("⛔ 已拒绝")
        assert app.query_one(AssistantResponse).answer_text == "写入已被用户拒绝。"
        assert app.conversation.snapshot()[0].assistant == "写入已被用户拒绝。"


class SixToolProvider:
    display_name = "six-tool-provider"
    model_name = "six-tool-model"

    def __init__(self) -> None:
        self.requests = (
            ("write_file", {"path": "acceptance-note.txt", "content": "KCode write passed"}),
            ("read_file", {"path": "acceptance-note.txt", "start_line": 1, "max_lines": 20}),
            (
                "edit_file",
                {
                    "path": "acceptance-note.txt",
                    "old_text": "KCode write passed",
                    "new_text": "KCode edit passed",
                },
            ),
            ("run_command", {"command": "pwd"}),
            ("find_files", {"root": ".", "pattern": "*.txt"}),
            (
                "search_code",
                {"root": ".", "pattern": "KCode edit passed", "file_pattern": "*.txt"},
            ),
        )
        self.calls = 0

    async def stream(self, messages, tools=(), tool_choice="auto"):
        request_index = self.calls // 2
        second_request = self.calls % 2 == 1
        self.calls += 1
        if second_request:
            yield TextDelta(f"{self.requests[request_index][0]} 已完成。")
            yield StreamCompleted("stop")
            return
        name, arguments = self.requests[request_index]
        yield ToolCallDelta(
            0,
            f"six-tool-{request_index}",
            name,
            json.dumps(arguments, ensure_ascii=False),
        )
        yield StreamCompleted("tool_calls")


async def test_six_tools_execute_and_render_clear_success_cards(tmp_path) -> None:
    app = KCodeApp(SixToolProvider(), cwd=tmp_path)
    expected_labels = ("新建文件", "读取文件", "修改文件", "执行命令", "查找文件", "搜索代码")
    async with app.run_test(size=(110, 40)) as pilot:
        for index, label in enumerate(expected_labels, 1):
            await submit(app, pilot, f"验收第 {index} 个工具")
            await pilot.pause(0.15)
            widgets = list(app.query(ToolCallWidget))
            assert len(widgets) == index
            widget = widgets[-1]
            assert label in widget.query_one(".message-role").render().plain
            assert widget.query_one(".tool-status").render().plain.startswith("✓ 执行成功")

    assert (tmp_path / "acceptance-note.txt").read_text(encoding="utf-8") == "KCode edit passed"

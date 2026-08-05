from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Label, Markdown, Static

from kcode.tools.base import ToolCall, ToolResult

TOOL_LABELS = {
    "read_file": "读取文件",
    "write_file": "新建文件",
    "edit_file": "修改文件",
    "run_command": "执行命令",
    "find_files": "查找文件",
    "search_code": "搜索代码",
}


def _short(value: object, limit: int = 320) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _arguments_summary(call: ToolCall) -> str:
    try:
        arguments = json.loads(call.arguments_json)
    except json.JSONDecodeError:
        return "参数格式无效\n" + _short(call.arguments_json)
    if not isinstance(arguments, dict):
        return _short(arguments)
    labels = {
        "path": "文件",
        "root": "目录",
        "pattern": "模式",
        "file_pattern": "文件模式",
        "command": "命令",
        "cwd": "工作目录",
        "start_line": "起始行",
        "max_lines": "最大行数",
        "old_text": "原文",
        "new_text": "替换为",
        "content": "内容预览",
    }
    return (
        "\n".join(f"{labels.get(key, key)}：{_short(value)}" for key, value in arguments.items())
        or "无参数"
    )


def _result_summary(call: ToolCall, result: ToolResult) -> str:
    if result.status != "success":
        error = result.error
        prefix = {
            "denied": "⛔ 已拒绝",
            "timeout": "⌛ 执行超时",
            "cancelled": "■ 已取消",
        }.get(result.status, "✗ 执行失败")
        return f"{prefix}\n[{error.code}] {error.message}" if error else prefix
    data = dict(result.data or {})
    path = data.get("path")
    if call.name == "write_file":
        detail = f"文件：{path}\n写入：{data.get('bytes_written', 0)} 字节"
    elif call.name == "edit_file":
        detail = f"文件：{path}\n替换：{data.get('replacements', 0)} 处"
    elif call.name == "read_file":
        detail = f"文件：{path}\n行：{data.get('start_line')}–{data.get('end_line')}"
    elif call.name == "find_files":
        detail = f"目录：{data.get('root')}\n找到：{len(data.get('matches', []))} 个文件"
    elif call.name == "search_code":
        detail = f"目录：{data.get('root')}\n找到：{len(data.get('matches', []))} 处匹配"
    elif call.name == "run_command":
        output = _short(data.get("stdout", ""), 500) or "（无）"
        detail = f"退出码：{data.get('exit_code')}\n输出：{output}"
    else:
        detail = _short(result.to_json(), 700)
    suffix = "\n结果已截断" if result.truncated else ""
    return f"✓ 执行成功 · {result.duration_ms} ms\n{detail}{suffix}"


class ChatMessageWidget(Vertical):
    def __init__(self, role: str, text: str = "", *, id: str | None = None) -> None:
        super().__init__(id=id, classes=f"message {role}")
        self.role = role
        self.text = text

    def compose(self) -> ComposeResult:
        yield Label("你" if self.role == "user" else "KCode", classes="message-role")
        yield Markdown(self.text or " ", classes="message-content")

    def update_text(self, text: str) -> None:
        self.text = text
        self.query_one(Markdown).update(text or " ")


class AssistantResponse(Vertical):
    def __init__(self, iteration: int | None = None) -> None:
        super().__init__(classes="message assistant")
        self.answer_text = ""
        self.thinking_text = ""
        self.iteration = iteration

    def compose(self) -> ComposeResult:
        suffix = f" · 第 {self.iteration} 轮" if self.iteration else ""
        yield Label(f"KCode · 模型回复{suffix}", classes="message-role")
        with Collapsible(title="Thinking", collapsed=False, id="thinking"):
            yield Markdown(" ", id="thinking-content")
        yield Markdown(" ", id="answer-content", classes="message-content")

    def update_answer(self, text: str) -> None:
        self.answer_text = text
        self.query_one("#answer-content", Markdown).update(text or " ")

    def set_iteration(self, iteration: int) -> None:
        self.iteration = iteration
        self.query_one(".message-role", Label).update(f"KCode · 模型回复 · 第 {iteration} 轮")

    def update_thinking(self, text: str) -> None:
        self.thinking_text = text
        self.query_one("#thinking-content", Markdown).update(text or " ")

    def finish_thinking(self) -> None:
        thinking = self.query_one("#thinking", Collapsible)
        if self.thinking_text:
            thinking.collapsed = True
        else:
            thinking.display = False


class ToolCallWidget(Vertical):
    def __init__(self, call: ToolCall) -> None:
        super().__init__(classes="message tool")
        self.call = call

    def compose(self) -> ComposeResult:
        label = TOOL_LABELS.get(self.call.name, "未知工具")
        yield Label(f"工具 · {label} ({self.call.name})", classes="message-role")
        yield Static(_arguments_summary(self.call), classes="tool-arguments", markup=False)
        yield Static("等待执行", classes="tool-status", markup=False)

    def set_running(self) -> None:
        self.query_one(".tool-status", Static).update("执行中…")

    def set_result(self, result: ToolResult) -> None:
        status = self.query_one(".tool-status", Static)
        status.remove_class("tool-success", "tool-error")
        status.add_class("tool-success" if result.status == "success" else "tool-error")
        status.update(_result_summary(self.call, result))

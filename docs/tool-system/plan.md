# KCode 工具系统 Plan

## 架构概览

在 Provider 与 TUI 之间增加 `TurnRunner`。Provider 只负责协议映射和流事件；`StreamAccumulator` 同时转发增量并收集完整响应；工具域负责注册、授权和执行；Conversation 只提交完整轮次。

## 核心接口

- 协议消息：`SystemMessage`、`UserMessage`、`AssistantMessage`、`ToolResultMessage`。
- 工具事件：`ToolCallDelta` 按 index 拼接为 `ToolCall`。
- 工具契约：`ToolSpec`、Pydantic 参数模型、`Tool.execute()`、`ToolResult`。
- 执行边界：`ToolRegistry`、`ToolPolicy`、`ToolExecutor`、异步 `ApprovalHandler`。
- 轮次边界：`TurnRunner.run()` 产生文本、思考、工具状态、通知和完成事件。

## 模块与数据流

六工具位于 `src/kcode/tools/`；`orchestration.py` 实现响应收集和单工具两请求流程；Provider 映射统一消息；Textual 仅消费轮次事件并提供授权回调。OpenAI 使用 Chat Completions，DeepSeek 复用该实现；Anthropic 保存完整 continuation state。第二次请求保留 tools 并设置 `tool_choice=none`。

## 技术决策

- 使用 Pydantic 本地严格验证，不依赖供应商 strict 扩展。
- 文件操作使用同目录临时文件与并发变化检查；遍历协作取消。
- 白名单简单命令不经 shell，其余批准后使用系统 shell和独立进程组。
- 工具结果以紧凑 UTF-8 JSON 回灌，已知密钥统一替换为 `[REDACTED]`。
- 版本升级为 0.2.0，不增加运行依赖。

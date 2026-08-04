# KCode 纯对话 MVP Spec

## 背景与目标

KCode 是一个从零开发的 Python 命令行 AI 助手。本阶段提供全屏终端对话、流式回答和当前进程内的多轮记忆，支持 Anthropic、OpenAI 和使用 OpenAI 兼容协议的 DeepSeek。

## 功能需求

- F1: `kcode` 与 `python -m kcode` 进入同一个全屏终端应用，退出后恢复终端。
- F2: 成功完成的问答在当前进程内形成多轮上下文。
- F3: 支持 Anthropic 和 OpenAI 两种协议；DeepSeek 复用 OpenAI 协议。
- F4: Provider 通过统一边界接入，便于增加后端。
- F5: YAML 保存多个 Provider，并用 `active_provider` 选择当前配置；每个完整 Provider 包含 `name`、`protocol`、`model`、`base_url`、`api_key` 和可选的 `thinking`。
- F6: 先加载用户配置，再用项目配置按字段覆盖同名 Provider。
- F7: `api_key` 支持直接值及 `${ENV_VAR}`，示例仅使用环境变量。
- F8: 配置无效时在 TUI 启动前报告文件、字段和修复建议。
- F9: 文本连续流入界面，并实时呈现 Markdown 与代码高亮。
- F10: Anthropic thinking 独立实时显示；其他协议忽略该选项并提示。
- F11: 生成时 Ctrl+C 取消回答，空闲时 Ctrl+C 退出；不完整回答不进入历史。
- F12: 认证、限流、网络及无效响应错误不重试，显示后可继续输入。
- F13: 支持 `/help`、`/clear`、`/exit`，未知命令不发送给模型。
- F14: 界面依次显示猫咪 banner、应用名与版本、工作目录、就绪行、可滚动聊天区、带 `❯` 的固定输入框和 Provider/模型状态栏。

## 非功能需求

- Python 3.11+，支持 macOS、Linux 与 Windows。
- 流式内容不等待完整响应才显示，密钥不得出现在输出和错误中。
- 使用 UTF-8，Provider 相互隔离，边界有类型定义且可独立测试。
- 默认测试不调用真实 API、不访问网络、不产生费用。
- 不持久化会话或改写配置；取消和退出正确释放资源。

## 不做的事

不实现工具调用、文件操作、代码编辑、ReAct、MCP、子 Agent、历史持久化、运行时切换 Provider、配置向导、多模态、自动重试、并发会话或用量遥测。

## 验收标准

- AC1-AC6: 两种入口一致；多轮与清空正确；三个后端走统一入口；配置合并、环境变量和错误提示可观测且不泄密。
- AC7: 80×24 及以上终端同时显示规定的七个界面区域，聊天区滚动而输入框和状态栏固定。
- AC8-AC10: 流结束前至少更新两次；thinking 行为正确；取消不提交历史且随后仍可提问。
- AC11-AC13: 四类错误可恢复；本地命令不访问模型；全套测试无真实 API 和网络。

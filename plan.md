# KCode 纯对话 MVP Plan

## 架构概览

启动层依次装载配置、创建 Provider 和会话服务，再启动 Textual 应用。配置层负责两级 YAML 合并及密钥解析；Provider 层把不同 SDK 映射为统一异步事件流；会话层只提交完整成功的回答；界面层只消费事件，不直接依赖 SDK。

## 核心接口

- `ProviderConfig`: 不可变配置，包含六个 Provider 字段。
- `AppConfig`: `active_provider`、Provider 字典和 `active` 访问器。
- `ChatMessage`: `role` 与 `content`。
- `ChatProvider.stream(messages)`: 返回 `TextDelta`、`ThinkingDelta`、`StreamCompleted` 的异步迭代器。
- `Conversation`: 构造请求快照、提交成功轮次、清空及查看历史。
- `ProviderError`: 统一认证、限流、网络和无效响应错误。

## 模块设计与交互

配置按用户文件、项目文件顺序读取，同名 Provider 按字段合并，最后统一校验并解析精确 `${VAR}`。Anthropic 使用 Messages 原始事件流映射文本和 thinking；OpenAI 使用 Chat Completions，DeepSeek 通过不同 `base_url` 复用。Textual Worker 消费流并把事件送回消息循环，界面最多每秒刷新 30 次；正常结束后提交历史，取消或错误则保留可见提示但不提交。

## 文件组织

采用 `src/kcode` 布局，分为 `config.py`、`events.py`、`errors.py`、`conversation.py`、`providers/`、`ui/` 与 `cli.py`；测试放在 `tests/`。

## 技术决策

- Textual 8.x 全屏 TUI，Markdown/Rich/Pygments 由 Textual 提供。
- 官方 Anthropic 0.x 和 OpenAI 2.x 异步 SDK。
- Pydantic 2 + PyYAML 6；Hatchling 打包；uv 锁定依赖。
- Anthropic `max_tokens=4096`，thinking budget `1024`。
- 版本 `0.1.0`，刷新上限 30 FPS。

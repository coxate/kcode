# Kcode SubAgent 机制 Spec

> 状态：已批准。

## 背景与目标

Kcode 已有 `AgentRunner`、Skill Fork、Hook、权限引擎和工具 Registry，但缺少统一的任务委派机制。本期实现：

- 定义式 SubAgent：从空白对话和预定义角色启动。
- Fork 式 SubAgent：继承父对话、Provider 和完整工具集。
- 前台运行、自动转后台、手动转后台和后台任务管理。
- 独立权限追踪、Token 统计和审批队列。
- 复用现有 Skill Fork，并补全 Hook 的 `agent` action。

## 功能需求

### F1：统一 Agent 工具

新增稳定的 `agent` 工具，参数为 `prompt`、`description`、可选 `subagent_type`、`run_in_background` 和 `name`。提供 `subagent_type` 时走定义式，省略时走 Fork。不允许调用时临时切换 Provider。

### F2：Agent 定义格式

定义使用 Markdown + YAML frontmatter，严格接受 `name`、`description`、`tools`、`disallowed_tools`、`model`、`max_turns`、`permission_mode` 和 `background`。正文是稳定角色提示。

- `name` 只允许小写 kebab-case。
- `model` 为 `inherit` 或 Kcode 配置中的 Provider 名称。
- `permission_mode` 使用 Kcode 现有四种模式。
- `tools` 为空表示不额外限制；`disallowed_tools` 始终优先。
- 未知字段、非法值、空正文、非法工具或 Provider 使单个外部定义失效并 warning；内置定义失效则 fail-fast。
- 拒绝符号链接、越界路径、二进制和非法 UTF-8。
- 单文件最大 32 KiB，描述为不超过 200 字符的单行文本，Catalog 最多 30 项。

### F3：加载来源与覆盖

优先级为项目 `> `用户 `>` 内置 `>` 插件：

1. `<project>/.kcode/agents/*.md`
2. `~/.kcode/agents/*.md`
3. Kcode wheel 内置定义
4. `~/.kcode/plugins/<plugin-id>/agents/*.md`

内置 `general-purpose`、`explore` 和 `plan`；全部默认继承 Provider，后两者只允许只读工具。

### F4：项目定义信任

项目 Agent 使用独立指纹信任，指纹覆盖规范化项目路径、排序后相对路径和原始字节。默认保存到 `~/.kcode/subagent-trust.json`，测试可用 `KCODE_SUBAGENT_TRUST_PATH` 覆盖。首次或内容变化时重新确认；拒绝后排除项目 Agent 但不阻止启动。启动后变化只使用已信任缓存并提示重启。

项目定义禁止 `bypassPermissions`。用户和用户插件定义可使用，但启动时必须警告。

### F5：运行时隔离

每个子 Agent 独立持有 Conversation、ContextManager、AgentSession、取消状态、临时审批状态、HookSession 和 Token 用量。共享 Provider 实例池、工具对象、文件系统、MCP 连接、权限规则、HookEngine 基础设施和 Skill Catalog。子 Token 计入自身任务和主 UI 会话费用，但不修改主 ContextManager 的估算锚。

### F6：定义式执行

定义式从空 Conversation 启动，Kcode 默认稳定 Prompt 叠加角色正文，用户任务作为首条 user message。模型不再调工具时完成。Provider 由角色决定，轮次上限由 `max_turns` 或全局配置决定，角色权限只能比父 Agent 更严格，不继承父 Agent 的单次审批。

### F7：Fork 执行

Fork 继承父 Agent 当前 Provider、完整规范消息前缀、完整工具 schema 和父权限模式，注入 Fork 任务边界提示，并始终后台运行。稳定 Prompt、工具定义和历史前缀保持一致，真实缓存命中以 Provider 返回的 cache token 为准。Fork 中的 SubAgent 控制工具保留 schema，但执行始终返回嵌套禁止错误。

### F8：工具过滤

定义式子 Agent 从父 Registry 开始，移除 SubAgent 控制工具，依次应用角色白名单、黑名单和后台限制。后台定义式 Agent 只保留 Kcode 六个基础工具、`load_skill` 和角色显式列出的 MCP。`load_skill` 不能扩大 Registry。Fork 完整继承父工具，由权限、审批、沙箱、危险命令黑名单和 Hook 控制。

### F9：权限与审批

权限严格度为 `plan < default < acceptEdits < bypassPermissions`，角色只能保持或收紧父权限。黑名单、沙箱、项目规则和 Hook 拦截始终生效。后台审批使任务进入 `waiting_approval`，按 FIFO 排队，主 TUI 前台生成结束后显示来源，拒绝结果返回子 Agent。

### F10：后台切换

支持显式 `run_in_background=true`、前台运行 120 秒自动转后台、前台 SubAgent 运行时按 Esc 转后台。`Ctrl+C` 仍表示取消。Fork 和 `background: true` 角色始终后台。

用户级配置新增 `subagents.enabled`、`background_enabled`、`auto_background_seconds`、`max_running` 和 `max_retained`，默认为 `true/true/120/4/20`。项目级同名配置被忽略并 warning。

### F11：任务管理

新增稳定的 `task_list`、`task_get`、`task_stop` 和 `task_send_message`，全部使用唯一 `task_id`。状态为 `pending/running/waiting_approval/completed/failed/cancelled`。最多同时运行或等待审批 4 个，最多保留 20 个；满额时先淘汰最旧已结束任务，无可淘汰项则拒绝新建。`task_send_message` 只续派已完成且仍保留的任务。

### F12：结果通知

后台任务结束后 UI 显示简短通知，下一次主模型迭代收到一次性 `<task-notification>`。通知包含 ID、名称、状态、结果和 Token，脱敏并限制 32 KiB，不写 session JSONL。应用退出时取消全部后台任务。

### F13：Skill Fork 兼容

Skill Fork 复用 SubAgent 的子 Runner 构造、隔离、用量和过滤底座，但 `/review` 等仍前台等待、完整回流并保持现有 session 历史语义。

### F14：Hook Agent Action

Hook `agent` action 必须提供 `prompt` 和 `subagent_type`，只能启动定义式后台 Agent，支持现有模板变量，必须 `async: true`，禁止用于 `pre_tool_use`、reject 和子 Agent Hook 来源。复用同一 TaskManager、权限边界和任务上限。

## 验收标准

- 四级来源加载、覆盖、严格校验、信任变化和边界值正确。
- 项目角色无法绕过父权限或启动孙 Agent。
- 定义式从空上下文开始，Fork 包含完整父请求前缀。
- 三种后台路径不丢失子任务，后台审批不打断正在进行的主 Agent 生成。
- 并发、保留、通知、结果大小、用量和退出清理严格生效。
- Skill Fork 行为不变，Hook agent action 能创建受控后台任务。
- 旧配置、旧 session、普通对话、Slash Command、Skill 和 Hook 保持兼容。

## 本期不做

- Worktree 或文件系统隔离。
- 多 Agent 团队编排、孙 Agent 或任意深度任务树。
- 后台任务与通知的跨会话持久化。
- 调用时临时 Provider 覆盖。
- 自动解决多 Agent 的语义级代码冲突。
- 结构化输出协议和 Verification Agent。

# Kcode SubAgent + 后台任务技术实施 Plan

> 状态：已批准。

## Summary

新增 `kcode.subagents` 领域包，复用现有 `AgentRunner`、Tool Registry、权限引擎、Skill Runtime 和 HookEngine，不另写 Agent Loop。启动顺序调整为定义发现与信任 → MCP 注册 → 最终校验 → 稳定工具与 Prompt 更新 → Hook 启动 → 启用输入。

## 核心类型与接口

- `AgentDefinition`、`AgentCatalog`、`AgentTrustStore`、`ProviderPool`、`SubAgentFactory` 和 `SubAgentService` 负责定义、路由和 Runner 构造。
- `TaskManager`、`TaskRecord`、`ApprovalBroker` 和 `DelegationSnapshot` 负责后台状态、请求前缀、审批、取消、通知和用量。
- `AppConfig` 新增用户级 `SubAgentConfig`；`AgentRunner` 新增只读 Delegation Snapshot、一次性 Fork 请求种子和任务通知源。
- `SystemReminderMessage` 新增 `task`；`ApprovalRequest` 新增可选来源；`ToolSpec` 新增由工具自管超时的选项。
- `HookContext` 新增内部 Agent 来源标记，`HookAction` 新增严格 `AgentAction`。

## Implementation Changes

### Catalog、信任与 Provider

- Parser 复用 Skill 的路径安全策略；Catalog 按 plugin → builtin → user → project 覆盖，MCP 后最终校验工具和 Provider。
- 项目指纹与信任原子存储；项目正文变化只执行已信任缓存。
- `ProviderPool` 复用主 Provider，其它已配置 Provider 按名称懒加载。
- 稳定 Prompt 新增只包含名称和描述的 `Available Agents`。

### 稳定工具与 Registry

- 首次模型请求前一次性注册 `agent`、`task_list`、`task_get`、`task_stop` 和 `task_send_message`。
- 定义式 Registry 移除 SubAgent 控制工具，应用角色白黑名单和后台限制。
- Fork Registry 完整复制父 schema，五个控制工具替换为同 schema 拒绝代理。
- Skill Fork 子 Runner 不暴露 SubAgent 控制工具。

### Runner 与 Fork

- 定义式子 Runner 使用空 Conversation、独立 ContextManager/AgentSession/SkillRuntime/InMemoryHookSession 和受限 Registry，共享权限规则和 Hook 基础设施。
- 有效权限取父模式和角色模式中更严格者；`max_turns` 映射到子 `AgentConfig`。
- Fork 从父 Runner 取得模型刚看到的真实消息前缀，首轮追加边界提示和任务，保持 Provider、稳定 Prompt、工具 schema 和模式一致。
- 首轮避免不必要重新压缩，超窗口时仍允许现有紧急压缩降级。

### 后台、审批与通知

- `TaskManager` 使用 `asyncio.Task`、Semaphore 和事件队列；120 秒或 Esc 只脱离前台等待者，不取消子 Runner。
- 前台正常完成释放临时记录；脱离后进入可查询保留集。退出时先取消子任务，再关闭 Hook 和 MCP。
- 后台审批使用 FIFO Broker，TUI 空闲后逐个显示，取消任务时撤销未处理审批。
- TaskManager 汇总子 Token 到任务和 UI session 费用，不调主 ContextManager `record_usage`。
- 完成通知通过 `task` System Reminder 一次性消费，不写 Conversation 或 JSONL。

### Hook、Skill 和 TUI

- Hook `agent` action 必须异步且只允许定义式；子 Agent Hook 来源被拒绝，避免递归。
- Skill Fork 只复用 Factory、隔离和用量底座，不进 TaskManager，保留 `/review` 前台回流。
- TUI 新增 AgentTrustScreen、条件 Esc 脱离、后台/审批数量状态和简短完成 notice。
- 输入框只在信任、MCP、Agent 最终校验、Prompt 和 Hook 绑定完成后启用。

## Test Plan

- Parser/Catalog/Trust/Config：四级覆盖、严格输入、路径边界、指纹、内置资源、用户级配置和旧配置兼容。
- Filtering/Permissions：白黑名单、后台基础集、显式 MCP、Fork 拒绝代理、父模式上限和审批隔离。
- Runtime/Background：空白定义式、Fork 完整前缀、Provider 路由、轮次、三种后台、Ctrl+C、4/20 上限、续派和关闭。
- Tools/Notifications：五个稳定 schema、结构化错误、嵌套拒绝、一次性通知、脱敏和 JSONL 隔离。
- Hook/Skill/Startup：agent action 约束、递归阻断、Skill Fork 回归、MCP 三条启动路径和输入启用时机。
- 全仓 pytest、ruff check、改动文件 format check、wheel 资源、`git diff --check` 和 tmux 真实端到端验证。

## Assumptions

- 基于已推送的 Slash Command、Skill 和 Hook 实现继续，当前基线为 `367 passed, 2 skipped`。
- 无 Worktree 隔离；文件工具的并发保护保留，`run_command` 变更仍由审批和 Hook 控制。
- 后台任务只存在于当前进程，`subagents` 配置只接受用户级。
- 本期不新增 `/tasks` Slash Command。

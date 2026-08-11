# Kcode Hook 生命周期挂钩系统 Plan

> 审批状态：已批准。

## 架构概览

新增独立 `kcode.hooks` 包，承载严格模型、条件解析、Catalog、项目指纹信任、session 运行状态、三种执行器与 HookEngine。Hook 通过显式接口横切 App、AgentRunner、ToolExecutor、CommandDispatcher 和 Skill fork，不依赖 Skill，也不混入现有 UI AgentEvent 流。

启动顺序：空 Engine → Skill 信任 → Hook 信任 → MCP → Skill/Hook Catalog → 注册命令 → 冻结 Registry → startup → session_start → 启用输入。各阶段独立降级。

## 核心类型与接口

- `HookEvent`：15个 snake_case 事件。
- `HookSource`、`ConditionOperator`、`ConditionJoin`、`Condition`、`ConditionGroup`。
- 严格判别联合 `CommandAction | PromptAction | HttpAction`。
- `Hook`：id、event、condition、action、reject/reason、once/async、source/order。
- `HookContext`：通用 session/cwd/mode 与事件特化字段，提供字段读取和模板变量。
- `HookWarning`、`HookDispatchResult`、`ToolRejectedError`、`HookSummary`、`HookCatalog`。
- `HookCatalogBuilder.trust_request/build`；`HookTrustStore.is_trusted/trust`。
- `HookRuntime`：once、prompt 队列、8个异步任务、warning queue、close。
- `HookEngine.run_hooks/run_pre_tool_hooks/set_catalog/summaries/close`。
- `HookActionExecutor.execute/close`，内部支持 command/prompt/http。

`SessionRuntime` 增加不持久化的 `executed_hook_ids` 与 `pending_hook_prompts`。`SystemReminderMessage.kind` 增加 `hook`。

工具准备拆为：

```text
ToolExecutor.validate
→ pre_tool_use
→ ToolExecutor.authorize 或 rejected
→ ToolScheduler
→ post_tool_use
→ 成功 write/edit 时 file_change
```

`prepare()` 保留为 validate+authorize 兼容包装。

## 模块设计

- `models.py`：不可变运行模型与严格 Action 配置。
- `parser.py`：YAML 单条解析、tokenizer、matcher 编译、普通/shell-safe 模板展开。
- `catalog.py`：安全读取、两层追加、ID冲突、预算和事件索引。
- `trust.py`：原始字节指纹、0700/0600、fsync、原子替换、测试路径覆盖。
- `runtime.py`：session 内存状态、prompt 原子预算、异步并发和 warning。
- `executor.py`：进程组 command、SystemReminder prompt、httpx AsyncClient。
- `engine.py`：条件、顺序、once、Plan Mode、普通 fail-open 与 reject fail-closed。
- `matching.py`：Hook 与权限共用内部 glob，不改变权限公开格式。

条件使用专用 tokenizer 支持引号/正则转义。`==/!=` 完整比较、`=~` regex search、`~=` 完整 glob；未知字段加载期拒绝。模板单次扫描，`$$` 保留 shell 变量，command 值经 `shlex.quote`。

普通分派按 Catalog 顺序执行；prompt 预算失败或 async 满额不标记 once；同步 action 已尝试即标记；reject 始终标记并停止后续 pre-tool Hook。

## 事件数据流

- Agent run：turn_start → 每轮 pre_send → Hook reminders → Provider → post_receive → 工具路径或终态 → error（如适用）→ turn_end。
- 自动/紧急/手动压缩共用 compact 出口。
- permission_request 在 ApprovalScreen 前，不可改变 ApprovalChoice。
- 参数解析失败不触发 pre-tool，但最终错误仍触发 post-tool。
- Skill fork 继承相同 Engine 与父 session Hook 状态，保留独立 Conversation、ContextManager 和工具白名单。
- CommandDispatcher 仅在命令和参数合法后触发 command_execute；handler 异常触发 error。
- clear/resume 在切换前 session_end，切换后 session_start；退出幂等触发 session_end/shutdown 后关闭 Engine。

## 技术决策

- 直接 await Engine，保证顺序和拦截，不复用 UI 事件总线。
- 项目 Hook 独立信任；信任后 action 不重复审批，但 Plan Mode 禁止 command/HTTP。
- reject 使用独立 reason，附加 action 失败不放行。
- 使用 `httpx>=0.27,<1` 直接依赖和共享 AsyncClient。
- 最多8个异步任务，无等待队列；普通失败 fail-open，reject fail-closed。
- prompt 只放 session 内存 reminder，真实 Token 计费但不入历史。
- 不声明 SubAgent 接口，后续章节重新设计。

## 测试设计

覆盖解析/预算/信任、条件与模板、三类执行器、Engine、两阶段工具权限、15事件、会话、命令、MCP、Skill fork、脱敏和取消。全仓必须高于 `321 passed, 2 skipped`，通过 ruff、format、diff、wheel，并用临时信任路径、本地 HTTP 与假 Provider 做 tmux 实跑。

# Kcode Agent Team MVP Plan

> 状态：已批准。基于已批准的 `spec.md` 与已验收的 Worktree M1。

## 架构概览

新增 `kcode.teams` 领域包，在现有主 Agent、定义式 SubAgent、TaskManager 和 WorktreeManager 之上增加一层单进程协作协调器。TeamManager 是唯一写入 Team 状态的入口，持有一个可选的活跃 Team、成员名册、共享任务板和内存邮箱；工具与 Slash Command 只调用它，不自行修改状态。

Team 不建立第二套 Agent Loop。每个成员仍由 `SubAgentFactory` 创建 `ChildAgent`，由 `TaskManager` 执行、计费、审批和取消。TaskManager 只增加通用的任务类别、保留式完成和完成回调；它不知道 Team 名称、任务板或消息规则。TeamManager 把一次成员运行结束映射为 `idle` 或 `failed`，并决定何时最终执行 Worktree finalizer。

主 Agent 与每个成员各绑定一个内存收件箱。`AgentRunner` 在每轮模型调用前读取收件箱，把消息渲染成不可信的 `<team-messages>` reminder。running 成员自然在下一轮读取；idle 成员由 TeamManager 通过 TaskManager 的受限续派接口异步唤醒；Lead 的收件箱永远不自动启动模型。

```text
Lead tools / Slash Commands
            │
            ▼
       TeamManager ───────────────┐
       │    │    │                │
       │    │    └─ TaskBoard     ├─ InMemoryMailbox ── AgentRunner reminders
       │    └────── Member roster │
       │                          │
       ├─ SubAgentFactory         │
       ├─ TaskManager ────────────┘
       └─ WorktreeManager
```

依赖方向保持单向：UI/Tools → TeamManager → SubAgent/Worktree；TaskManager 只通过通用回调向上报告一次运行结束，不反向导入 `kcode.teams`。

## 核心数据结构与接口

### `TeamConfig`

Pydantic 不可变配置：

- `enabled: bool = False`
- `max_members: int = Field(default=3, ge=1, le=3)`

`AppConfig` 增加 `teams` 与 `team_warnings`。合并配置时只采纳用户级 `teams`；项目级只要出现该段就整段忽略并产生 warning。

### 身份与状态

`validate_team_slug(value)` 与 Worktree slug 采用相同字符约束，但返回 Team 领域错误：`^[a-z0-9][a-z0-9-]{0,63}$`。Team 名、成员名都必须通过它。

字符串枚举：

- `TeamMemberStatus`: `starting | running | idle | stopping | stopped | failed`
- `TeamTaskStatus`: `pending | in_progress | completed | cancelled`
- `IsolationMode`: `shared | worktree`
- `TaskKind`: 在现有 SubAgent 运行时增加 `subagent | team_member`

### `Team`

可变的进程内聚合根：

- `id: str`：随机生成、不会暴露为模型参数的 Team 世代 ID，防止旧成员工具误连到后创建的同名 Team。
- `name: str`、`goal: str`、`created_at: float`
- `members: dict[str, TeamMember]`
- `tasks: dict[str, TeamTask]`
- `mailbox: TeamMailbox`

同一 Team 对象存活期间不移除停止/失败成员名称；只有成功 `team_delete` 才销毁整个聚合根。

### `TeamMember`

- `name: str`
- `task_id: str`：TaskManager 内部记录 ID。
- `subagent_type: str`
- `isolation: IsolationMode`
- `status: TeamMemberStatus`
- `worktree: WorktreeRecord | None`
- `worktree_report: Mapping[str, JSONValue] | None`：最终清理/保留报告的结构化快照。
- `last_result: str`、`last_error: str`
- `wake_scheduled: bool = False`：合并 idle 唤醒并覆盖“最后一轮结束”竞态。
- `created_at: float`、`updated_at: float`

Conversation、Session、Runner 和累计 Token 仍由对应 `TaskRecord.child` 与 `TaskRecord.usage` 持有，TeamMember 不复制第二份真相。

### `TeamTask`

- `id: str`：`team-task-<12 hex>`。
- `title: str`、`description: str`
- `status: TeamTaskStatus = pending`
- `assignee: str | None`：`lead`、现有成员名或未分配。
- `blocked_by: frozenset[str]`
- `created_by: str`、`created_at: float`、`updated_at: float`

`ready` 不持久化，由查询时计算：所有 `blocked_by` 任务均为 completed 才为真。

### `TeamMessage` 与 `TeamMailbox`

`TeamMessage` 是不可变值：`sender`、`recipient`、`body`、`created_at`、单调递增 `sequence`。发送者不出现在工具参数中。

`TeamMailbox` 为每个参与者维护一个有序 `deque`，公开同步小接口：

- `register(participant) -> None`
- `send(sender, to, body) -> DeliveryResult`
- `take(participant) -> tuple[TeamMessage, ...]`
- `pending(participant) -> int`
- `clear() -> None`

所有调用发生在同一事件循环线程；TeamManager 的异步锁保护注册、寻址和发送的原子边界。`take` 一次取走当时全部消息，Runner 无需在模型迭代中等待另一把异步锁。

### `TeamCaller`

每个 Team 工具实例绑定不可伪造调用者：

- Lead 工具：`role="lead"`，由主 Registry 长期持有。
- 成员工具：`role="member"`、`member_name` 和创建时的 `team_id`。

TeamManager 每次调用都验证 Caller。成员工具即使被旧 Runner 保留，也无法操作已删除后新建的 Team。

### `TeamManager`

构造依赖：`TeamConfig`、`SubAgentConfig`、AgentCatalog、SubAgentFactory、TaskManager、父 AgentRunner、WorktreeManager、敏感值集合。公开异步接口与九个工具一一对应：

```python
create(caller, name, goal) -> TeamOperationResult
spawn(caller, name, prompt, subagent_type, isolation) -> TeamOperationResult
status(caller) -> TeamOperationResult
stop(caller, name) -> TeamOperationResult
delete(caller) -> TeamOperationResult
send_message(caller, to, message) -> TeamOperationResult
task_create(caller, title, description, assignee, blocked_by) -> TeamOperationResult
task_list(caller, status) -> TeamOperationResult
task_update(
    caller, task_id, status, assignee, add_blocked_by, remove_blocked_by
) -> TeamOperationResult
close() -> tuple[str, ...]
```

Manager 另有 `set_catalog(catalog)`，与现有 SubAgentService 在 Agent 信任解析后的更新时机一致。所有领域错误携带稳定 code，例如 `teams_disabled`、`no_active_team`、`team_exists`、`unknown_member`、`member_not_resumable`、`task_dependency_cycle`。

## 模块设计

### 配置与费用门禁

- `config.py` 按现有 `subagents` 用户级合并方式增加 Team 配置，项目配置不能覆盖任何字段。
- 九个 Team 工具无条件注册，因此 enabled 切换不改变主 Agent Schema。
- 每个 TeamManager 公开操作先检查 `teams.enabled`；关闭时在任何名称解析、Worktree 检查或 TaskManager 分配之前返回 `teams_disabled`。
- Team 还依赖 SubAgent 后台运行。若 Team 已开启但 `subagents.enabled` 或 `background_enabled` 关闭，create 可以保存协调目标，但 spawn 返回明确的 `subagents_disabled` 或 `background_disabled`，不静默更改用户配置。

### TaskManager 的通用生命周期扩展

`TaskRecord` 增加：

- `kind: TaskKind = SUBAGENT`
- `retain_on_success: bool = False`
- `pinned: bool = False`
- `completion_callback: TaskCompletionCallback | None`

现有 `TaskFinalization` 以向后兼容的默认值增加 `details: Mapping[str, JSONValue]`，`TaskRecord` 保存最后一次 finalization。Worktree finalizer 同时返回用于人类阅读的 suffix 和结构化的 path/branch/base/HEAD/dirty/kept/reason；TeamManager 因此不解析展示文本，也能在 Worktree 已安全删除后保留准确报告。

新增通用接口：

```python
TaskCompletionCallback = Callable[[TaskRecord], Awaitable[None]]

launch(..., kind=SUBAGENT, retain_on_success=False,
       pinned=False, completion_callback=None) -> LaunchResult
resume_retained(task_id, prompt, *, expected_kind) -> LaunchResult
finalize_retained(task_id, *, expected_kind) -> TaskRecord
wait(task_id, timeout) -> TaskRecord | None
release(task_id, *, expected_kind) -> bool
```

现有查询与取消接口增加默认保持兼容的 kind 边界：`summaries(kind=SUBAGENT)`、`get(task_id, expected_kind=SUBAGENT)`、`stop(task_id, expected_kind=SUBAGENT)`。普通 Service 不传参数时只能看到和操作普通 SubAgent；TeamManager 显式传 `TEAM_MEMBER`。

执行顺序固定为：确定一次运行的 terminal 状态 → completed 且 `retain_on_success` 时暂不调用 finalizer，否则恰好一次 finalizer → 调用 completion callback → 仅普通 SubAgent生成现有 task notification。

Team 成员首次及每次续派均使用 `kind=TEAM_MEMBER`、`retain_on_success=True`、`pinned=True`：

- 自然完成后 TaskRecord 保持 completed、Conversation/Worktree 可续用；回调把 TeamMember 改为 idle。
- 失败或取消会先执行 finalizer，再回调，TeamManager 可把最终 Worktree 报告投递给 Lead。
- stop/delete 对 idle 成员显式调用 `finalize_retained`，之后才可 `release`。
- `resume_retained` 同时校验 kind、completed 状态、未消费 finalizer和全局并发容量；普通 `task_send_message` 只允许 `SUBAGENT`，TeamManager 只允许 `TEAM_MEMBER`。
- 普通 `summaries/get/stop/send_message` 过滤 `SUBAGENT`；TeamManager 使用带 expected_kind 的内部接口，Team 成员不会混入普通 `task_*`。
- 容量回收只能逐出未 pinned 的 terminal 普通任务。活跃 Team 的 idle 成员占 retained 容量但不占 running 容量；达到上限时 spawn 在创建 Worktree 前拒绝。

completion callback 抛错只记录脱敏 warning，不能把已完成任务改写为成功或丢弃 finalizer。TaskManager 不导入任何 Team 类型。

### 成员创建与回滚

`team_spawn` 采用“预留—外部创建—发布”三阶段，避免持锁等待 Git 或模型任务：

1. 先用 TaskManager 的无副作用 ID 生成器预分配 `task_id`；再在 TeamManager 锁内验证 Caller、成员名唯一、成员数、Agent 定义、Team/TaskManager 容量与 isolation，并放入带该 ID 的 `starting` 占位成员。
2. 释放锁。worktree 模式调用现有 `WorktreeManager.create_agent(task_id)`；shared 模式不创建目录。
3. 复制 Worktree `ToolContext`（shared 则用父 Context），由 Factory 构造绑定成员协作工具的 ChildAgent，再调用 TaskManager background launch。
4. 重新加锁，确认 Team 世代与占位仍一致，将成员置为 running 并返回。
5. 任一步骤失败都撤销占位；若已创建 Worktree，则调用同一 `finalize(record, task_id)`，把清理或保留报告附到结构化错误。

TeamManager 不在持有自身锁时等待 TaskManager completion callback；stop/delete 同样先记录状态、释放锁，再 cancel/wait/finalize，防止回调反向获取锁造成死锁。

默认 worktree 不可用或主目录 dirty 时原样返回 M1 错误，不降级 shared。shared 只接受显式参数，并在状态、Prompt 与结果中显示并发写冲突提示。

### Factory 与工具边界

`SubAgentFactory` 增加专用 `team_member(...)` 构造入口，内部仍复用定义式 Agent 的 Provider、角色正文、最大轮次、权限收窄、Skills 和 Hooks 初始化。它额外接收：

- 已隔离的 `ToolContext`；
- `<team-context>` 角色说明；
- 绑定该成员身份的四个协作工具；
- 成员收件箱 reminder source。

`subagents.filter` 把全部九个 `team_*` 加入控制工具集合，确保普通定义式 SubAgent、Fork 和 Skill Fork不会从父 Registry 继承它们。Team member registry 先按角色工具、background 安全集和 disallowed_tools 生成基础 Registry，再显式注册：

- `team_send_message`
- `team_task_create`
- `team_task_list`
- `team_task_update`

成员永远不注册 `agent`、普通 `task_*`、`team_create`、`team_spawn`、`team_status`、`team_stop` 或 `team_delete`。Registry 隔离之后，TeamManager 的 Caller 校验仍作为第二层防线。

### 共享任务板

`TaskBoard` 在 TeamManager 锁内工作，提供 create/list/update：

- create 先验证标题、描述、负责人和全部依赖存在，再生成 ID 并提交。
- update 在一个临时副本上同时应用 status、assignee、add/remove dependencies；验证成功后一次替换原对象，失败不产生部分更新。
- 环检测使用 DFS 三色或 Kahn 拓扑排序，覆盖新增多条边后的完整图。
- pending → in_progress 仅在全部依赖 completed 时允许；pending/in_progress 可转 completed/cancelled；completed/cancelled 不再变化。
- `assignee=None` 在 update 表示“不修改”，空字符串表示清除负责人；其余值只能是 lead 或当前名册成员。停止/失败成员不能接收新的 in_progress 任务。
- list 的 status 是固定枚举过滤，结果包含 `ready` 和未完成依赖列表。

任务板是协作信息，不直接调度成员；成员是否开始工作仍由消息或首次 spawn prompt 驱动，避免任务状态修改暗中产生费用。

### 消息、Reminder 与 idle 续派

`AgentRunner` 增加独立的 `TeamMessageSource` 协议和 `bind_team_messages(source)`。每次模型迭代组装 reminder 时调用 source，按 sequence 排序后生成一个边界明确的 `<team-messages>` 块：消息被标注为不可信协作数据，不得覆盖 System Prompt、权限或项目规则。

发送流程：

1. TeamManager 根据绑定 Caller 得到 sender，校验目标和目标状态；`*` 展开为除 sender 外的 Lead 与所有成员。单播 stopped/failed 成员直接失败；广播预检中只要包含终态成员就整体返回 `member_not_resumable`，且不投递给任何人，避免“部分失败但调用者误以为全部送达”。
2. 正文先做长度校验与敏感值脱敏，再按每个收件人顺序入队。
3. running/starting 目标只入队，其 Runner 在下一轮读取。
4. idle 目标在锁内原子改为 running，然后释放锁，调用 `resume_retained`。续派 prompt 只说明“读取 Team reminder 并继续协作”，实际消息仍从收件箱读取。
5. 多条并发消息中只有第一个观察到 idle 的发送操作触发续派；后续消息看到 running，只追加同一队列。
6. 若续派因全局并发或关闭失败，成员恢复 idle，消息保留未读，返回错误且不产生第二次自动重试。
7. 若消息在成员最后一次 reminder 读取之后、自然完成回调之前到达，completion callback 会在同一锁内发现未读队列，并以 `wake_scheduled` 只安排一次延后续派；因此“发送时看到 running”不会造成消息永久滞留，也不会在当前 `_run` 尚未退出时重入同一 TaskRecord。

Lead inbox 在 App 初始化时绑定到主 Runner。Lead 正在生成时下一迭代读取；不在生成时只保留，等待下一次用户消息启动正常 run。TeamManager 不持有调用主 Runner `run()` 的能力。

成员自然完成的结果通知也创建内部 `TeamMessage(sender=<member>, recipient="lead")`。idle 报告通过 `WorktreeManager.status(record.name)`做只读检查，不调用 finalize；失败、stop 或 delete 报告使用最终 `WorktreeFinalizationReport`。

### 输出保护与成果报告

新增 Team 渲染辅助函数，复用 TaskManager 的 32 KiB 和敏感值替换规则，而不是把 Python 异常、Provider 原文或秘密直接放入消息。

- 普通正文先截断，固定的 Team 身份、Token 与 `<worktree-result>` 尾部预留字节预算，保证 review 路径和保留原因不被长结果挤掉。
- `team_status` 返回结构化数据：Team 名称/目标、成员状态/isolation/tokens/当前任务/Worktree、任务板各状态数量、Lead 和成员待读消息数。
- shared 成员明确输出 `isolation=shared` 与冲突 warning，不伪造 Worktree 字段。
- 不执行任何 commit、merge、cherry-pick 或分支删除之外的 Git 收敛动作；临时分支删除仍只发生在 M1 已证明“无成果”的 finalizer 中。

### stop、delete 与退出

`team_stop(name)`：

- starting/running：锁内置 stopping，锁外调用 Runner cancel，并在固定 5 秒窗口内等待 TaskManager；结束回调根据“stop 已请求”映射为 stopped，而不是 failed。
- idle：锁外 `finalize_retained`，保存报告，再置 stopped。
- stopped/failed：幂等返回现状和已有报告。
- 超时不强杀或删除 Worktree；返回 stopping 和保留提示，后续 completion callback 负责最终收敛。

`team_delete()` 先在锁内拒绝任何 starting/running/stopping 成员。对 idle 成员逐个执行与 stop 相同的 finalizer；确认都不再运行后，释放对应 pinned TaskRecord，清空任务板、邮箱和名册，最后把 active Team 设为空。工具结果先保存所有 Worktree 报告，再销毁内存对象。

App 退出顺序改为：`TeamManager.close()` → `TaskManager.close()` → Hooks/Session/Memory/MCP。Team close 先阻止新操作、并发取消所有成员，并在一个共享的 5 秒窗口内等待，而不是每个成员串行等待；TaskManager 的现有有界关闭负责回收仍在执行的 Runner、审批请求和 finalizer。退出报告无法显示在已关闭 TUI 时写入 warning/stderr；任何未知 Worktree 状态仍保留元数据和目录。

### 工具、Slash Command 与 App 接入

- `teams.tools` 定义九个固定 Pydantic 参数模型和工具类；主 Registry 无条件注册 Lead 版本。
- `team_status`、`team_task_list` 声明为 read-only；创建、成员控制、发消息和任务变更声明为 side-effect。Plan Mode 因此只能观察 Team，不能通过协作工具产生新成员、费用或状态变化。
- `team_task_update` 的可选字段以“不提供=不修改”为语义；`assignee=""` 表示清除负责人。依赖列表去重后验证。
- 所有 Team 工具返回 `ToolResult` 的稳定 code/data；不把活跃成员列表编码进 ToolSpec。
- 新增一个 `/team` 本地命令，解析 `status`、`stop <member>`、`delete`，并委托 App 的三个 CommandHost 方法。输出不进入 Conversation。
- App 构造并注入一个 TeamManager，给主 Runner 绑定 Lead inbox；Agent/MCP/Trust catalog 更新后同时刷新 SubAgentService 与 TeamManager。
- `/status` 保持现有字段；Team 的详细信息只在 `/team status`，避免关闭 Team 时改变旧输出。

## 模块交互

### 创建并运行成员

```text
team_spawn
  → TeamManager 预检并预留 starting 成员
  → WorktreeManager.create_agent（默认，先检查主目录干净）
  → SubAgentFactory.team_member（隔离 Context + 绑定协作工具/inbox）
  → TaskManager.launch(kind=team_member, retain_on_success=true, pinned=true)
  → 成员 AgentRunner 运行、审批、计费
  → 自然完成：不 finalize → completion callback → member idle
  → 结果 + Token + Worktree status 进入 Lead inbox
```

### 消息唤醒 idle 成员

```text
team_send_message（sender 由工具绑定）
  → TeamManager 校验目标并有序入队
  → 原子 idle → running
  → TaskManager.resume_retained(expected_kind=team_member)
  → 原 ChildAgent / Conversation / Session / Worktree 再次运行
  → AgentRunner 下一轮取出 <team-messages>
```

### 安全停止与删除

```text
team_stop / team_delete / App exit
  → 先阻止新续派
  → running: cancel + bounded wait
  → idle: finalize_retained
  → WorktreeManager.finalize（无成果清理；有成果/未知保留）
  → 保存并返回 review 报告
  → delete 才释放 TaskRecord 与内存 Team 状态
```

## 文件组织

```text
src/kcode/teams/
├── __init__.py       # 稳定导出
├── models.py         # Team/Member/Task/Message、枚举、领域错误
├── mailbox.py        # 有序进程内邮箱与 reminder source
├── task_board.py     # 任务状态、依赖验证与原子更新
├── manager.py        # Team 聚合根、成员生命周期、并发与清理
├── rendering.py      # 脱敏、限长、状态与成果报告
└── tools.py          # 九个固定工具及 Lead/Member 身份绑定

src/kcode/subagents/
├── manager.py        # TaskKind、保留式完成、callback、受限续派/释放
├── factory.py        # team_member 构造入口
└── filter.py         # Team 工具过滤边界

src/kcode/
├── config.py         # TeamConfig 与用户级合并
├── orchestration.py  # TeamMessageSource reminder 注入
├── commands/         # /team 子命令
├── ui/app.py         # Manager 组装、状态命令与退出顺序
└── cli.py            # 配置/warning 注入

tests/
├── test_team_config.py
├── test_team_mailbox.py
├── test_team_task_board.py
├── test_team_manager.py
├── test_team_tools.py
├── test_team_commands.py
├── test_team_subagents.py
└── test_team_integration.py
```

最终集成阶段再修改 `README.md`、`config.example.yaml`、`src/kcode/__init__.py`、`pyproject.toml` 和版本级 Checklist；文档阶段不提前改版本。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Team 后端 | 单进程 TeamManager + 内存对象 | 符合 MVP 边界，无文件邮箱、锁文件和恢复协议 |
| 运行模型 | 复用 ChildAgent 与 TaskManager | 权限、审批、取消、Token 和 Provider 行为只有一份实现 |
| idle 生命周期 | TaskRecord completed 但 pinned，延迟 finalizer | 保留原 Conversation 与 Worktree，同时不占运行槽位 |
| 普通任务隔离 | TaskKind + expected_kind 接口 | Team 不混入 `task_*`，也不能靠猜 task_id 绕过 TeamManager |
| 消息身份 | 工具实例绑定 Caller + Team 世代 ID | 模型没有 sender 参数，旧成员工具也不能连接新 Team |
| 消息投递 | 每参与者有序 deque + Runner reminder | running 自然收信，Lead idle 不自动产生费用 |
| idle 唤醒 | TeamManager 原子状态迁移后调用受限 resume | 多条消息只启动一次，失败仍保留未读消息 |
| 任务依赖 | 内存 DAG，副本验证后一次提交 | 环和非法边不会留下部分更新 |
| 默认隔离 | 复用 `create_agent`/`finalize` | 建立在已验收 M1 所有权与 fail-closed 规则上，不复制 Git 逻辑 |
| shared 模式 | 仅显式参数允许 | 支持非 Git/只读场景，同时让写冲突风险保持可见 |
| stop/delete | 两阶段、锁外等待 | 防止 TaskManager completion callback 与 TeamManager 互锁 |
| Git 收敛 | 只报告，不自动 merge/commit | 成果选择和冲突解决继续由用户批准后的 Lead 操作 |
| 工具 Schema | 九个主工具始终注册 | Team 开关或运行状态不造成 Provider 工具缓存漂移 |

## Spec 覆盖

- F1–F4：TeamConfig、稳定 Lead 工具、结构化错误、Slash Command。
- F5–F8：成员预留/回滚、Factory、TaskKind、idle 生命周期和 Registry/Caller 双层边界。
- F9：TaskBoard 的原子 DAG 验证、状态与负责人规则。
- F10–F11：绑定身份、Mailbox、Runner reminder、running 投递与 idle 单次续派。
- F12：复用权限/审批/Hook/Context，shared 风险显式化。
- F13–F14：延迟 finalizer、stop/delete/exit 两阶段流程与只报告不收敛。
- F15：结构化状态、输出保护、文档/版本/打包集成点。
- N1–N8 均由默认关闭、单事件循环锁、稳定 Schema、fail-closed 清理与分层测试覆盖。
- 无未归属需求；依赖图无环，TaskManager 和 WorktreeManager 都不反向依赖 Team 包。

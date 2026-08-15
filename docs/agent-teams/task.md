# Kcode Agent Team MVP Tasks

> 状态：已批准。基于已批准的 `spec.md` 与 `plan.md`。四份文档全部批准前不得编写实现代码；开发时按依赖顺序执行，每项先写失败测试，再实现到通过。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/kcode/teams/__init__.py` | 导出 Team 稳定公共类型与注册入口 |
| 新建 | `src/kcode/teams/models.py` | Team、成员、任务、消息、Caller、枚举和领域错误 |
| 新建 | `src/kcode/teams/mailbox.py` | 有序内存邮箱、参与者收件源和 reminder 渲染 |
| 新建 | `src/kcode/teams/task_board.py` | 任务状态、负责人、依赖 DAG 与原子更新 |
| 新建 | `src/kcode/teams/rendering.py` | 敏感值替换、32 KiB 限制、状态和成果报告 |
| 新建 | `src/kcode/teams/manager.py` | 单 Team 聚合、成员生命周期、消息、stop/delete/close |
| 新建 | `src/kcode/teams/tools.py` | 九个固定 `team_*` 工具和绑定 Caller |
| 修改 | `src/kcode/config.py` | `TeamConfig`、用户级合并、项目配置 warning |
| 修改 | `src/kcode/orchestration.py` | TeamMessageSource 绑定和逐轮 reminder 注入 |
| 修改 | `src/kcode/subagents/models.py` | `TaskKind` 等共享运行枚举 |
| 修改 | `src/kcode/subagents/manager.py` | 类别隔离、pinned 保留、callback、续派和最终释放 |
| 修改 | `src/kcode/subagents/factory.py` | Team member Runner、Registry、Context 与 Team Prompt |
| 修改 | `src/kcode/subagents/filter.py` | 普通 SubAgent 与 Team 成员的工具边界 |
| 修改 | `src/kcode/subagents/service.py` | 普通 Worktree finalizer 的结构化报告兼容 |
| 修改 | `src/kcode/worktrees/models.py` | FinalizationReport 的稳定结构化表示 |
| 修改 | `src/kcode/commands/models.py` | `/team` 所需 CommandHost 接口 |
| 修改 | `src/kcode/commands/builtins.py` | `/team status|stop|delete` 解析、注册与帮助 |
| 修改 | `src/kcode/ui/app.py` | TeamManager 组装、工具/Inbox/命令接线与退出顺序 |
| 修改 | `src/kcode/cli.py` | Team 配置、warning 与 App 注入 |
| 修改 | `config.example.yaml` | 默认关闭的 Team 配置和费用提示 |
| 修改 | `README.md` | Team 用法、隔离、费用、生命周期与收敛边界 |
| 修改 | `src/kcode/__init__.py`、`pyproject.toml` | M2 验收后统一升级到 `0.8.0` |
| 新建 | `tests/test_team_config.py` | 配置门禁与费用默认安全 |
| 新建 | `tests/test_team_mailbox.py` | 顺序、广播、身份与 reminder 边界 |
| 新建 | `tests/test_team_task_board.py` | 状态、负责人、DAG 和原子性 |
| 新建 | `tests/test_team_manager.py` | Team/成员/消息/stop/delete/close 生命周期 |
| 新建 | `tests/test_team_tools.py` | 九个 Schema、ToolEffect、Caller 与结构化错误 |
| 新建 | `tests/test_team_commands.py` | Slash Command、Host 委托和帮助 |
| 新建 | `tests/test_team_subagents.py` | Factory、工具过滤、Conversation 与审批复用 |
| 新建 | `tests/test_team_integration.py` | 真实 Git Worktree、并发成员和应用退出场景 |
| 修改 | `tests/test_subagent_manager.py` | TaskKind/pinned/callback 与普通任务兼容 |
| 修改 | `tests/test_subagent_filter.py`、`tests/test_subagent_factory.py` | Team 工具边界和成员构造 |
| 修改 | `tests/test_tool_orchestration.py` | Team reminder 的下一轮投递和 Lead 不自启 |
| 修改 | `tests/test_config.py`、`tests/test_cli.py`、`tests/test_commands.py`、`tests/test_app.py` | 接线和旧行为回归 |
| 新建 | `docs/agent-teams/checklist.md` | M2 行为验收清单，在下一阶段生成并审批 |
| 新建 | `docs/releases/0.8.0-checklist.md` | M1+M2 版本级集成验收，在下一阶段一并生成并审批 |

## 第一阶段：领域模型、配置与纯内存组件

### T1：定义 Team 领域错误、枚举与 slug

**文件：** `src/kcode/teams/models.py`、`tests/test_team_manager.py`  
**依赖：** 无

**步骤：**

1. 先写非法 Team/成员名称测试，覆盖空值、`.`、`..`、大小写、空白、斜杠、反斜杠、遍历和超过 64 字符。
2. 定义 `TeamError(code, message)`、`TeamMemberStatus`、`TeamTaskStatus`、`IsolationMode` 与 `validate_team_slug`。
3. 仅接受最长 64 字符的单段小写 slug，错误不包含未过滤路径详情。

**验证：** `uv run pytest tests/test_team_manager.py -q -k slug`，合法/非法矩阵全部通过。

### T2：定义 Team 聚合数据模型

**文件：** `src/kcode/teams/models.py`、`tests/test_team_manager.py`  
**依赖：** T1

**步骤：**

1. 定义 `Team`、`TeamMember`、`TeamTask`、`TeamMessage`、`TeamCaller` 和操作结果模型。
2. 固定 `team-task-<12 hex>` ID、Team 世代 ID、UTC/单调时间用途及 `wake_scheduled` 初值。
3. 让 Conversation、Runner、Session 和 Token 只由 TaskRecord持有，模型中不复制运行对象。

**验证：** 运行模型构造测试，确认默认状态、ID 格式、成员终态和 JSON 输出稳定。

### T3：加入 Team 配置模型

**文件：** `src/kcode/config.py`、`tests/test_team_config.py`  
**依赖：** 无

**步骤：**

1. 先写 `enabled=false`、`max_members=3` 默认值和 1～3 边界测试。
2. 定义不可变 `TeamConfig`，把 `teams` 与 `team_warnings` 加入 `AppConfig`。
3. 保持不含 `teams` 的旧配置可正常解析。

**验证：** `uv run pytest tests/test_team_config.py tests/test_config.py -q -k 'team or default'`。

### T4：实现用户级配置门禁

**文件：** `src/kcode/config.py`、`tests/test_team_config.py`  
**依赖：** T3

**步骤：**

1. 用户级 `teams` 正常合并；项目级只要出现该段便整段忽略。
2. 项目配置不能开启 Team 或提高成员上限，并产生包含用户配置位置提示的 warning。
3. 非 mapping 和越界值沿用 ConfigError 的安全错误格式。

**验证：** 用户开启、项目开启、项目覆盖、非法类型与旧配置测试全部通过。

### T5：实现 Team 输出保护

**文件：** `src/kcode/teams/rendering.py`、`tests/test_team_manager.py`  
**依赖：** T2

**步骤：**

1. 写敏感值替换、多字节字符和超过 32 KiB 的失败测试。
2. 实现消息、成员结果和状态报告的统一脱敏/限长助手。
3. 为固定的成员身份、Token 与 Worktree review 尾部预留预算，长正文不得截掉路径和保留原因。

**验证：** `uv run pytest tests/test_team_manager.py -q -k 'redact or truncat or report'`。

### T6：实现单播有序邮箱

**文件：** `src/kcode/teams/mailbox.py`、`tests/test_team_mailbox.py`  
**依赖：** T2、T5

**步骤：**

1. 实现参与者注册、单调 sequence、单播入队、pending、一次性 take 和 clear。
2. 未注册发送者/收件人、空正文和过长正文在入队前失败。
3. 确认不同收件箱独立、单一收件人严格保持发送顺序。

**验证：** `uv run pytest tests/test_team_mailbox.py -q -k 'single or order or clear'`。

### T7：实现原子广播和终态预检

**文件：** `src/kcode/teams/mailbox.py`、`tests/test_team_mailbox.py`  
**依赖：** T6

**步骤：**

1. `to="*"` 展开为除发送者外的全部参与者。
2. TeamManager 提供的可投递状态预检失败时，任何收件箱都不能收到部分广播。
3. 结果返回实际收件人和将被唤醒的成员占位字段，不允许静默丢弃。

**验证：** 广播成功、无收件人、包含 stopped/failed 目标和回滚测试通过。

### T8：实现 Team reminder 源

**文件：** `src/kcode/teams/mailbox.py`、`tests/test_team_mailbox.py`  
**依赖：** T6

**步骤：**

1. 为单一 participant 建立 `TeamMessageSource`，一次取出当时全部消息。
2. 按 sequence 渲染一个 `<team-messages>` 块，包含 sender/recipient/time 和“不可信协作数据”边界。
3. 正文中的伪 XML、System Prompt 指令和敏感值只能作为转义/分隔后的数据出现。

**验证：** reminder 顺序、一次性消费、Prompt 注入边界和脱敏测试通过。

### T9：实现任务板创建与列表

**文件：** `src/kcode/teams/task_board.py`、`tests/test_team_task_board.py`  
**依赖：** T2

**步骤：**

1. 实现任务 ID、标题、描述、创建者、负责人、依赖和默认 pending 状态。
2. 校验负责人只能是空、lead 或名册成员，依赖必须已存在且不能是自身。
3. list 支持固定状态过滤，并计算 ready 与未完成依赖。

**验证：** `uv run pytest tests/test_team_task_board.py -q -k 'create or list or assignee'`。

### T10：实现任务状态机

**文件：** `src/kcode/teams/task_board.py`、`tests/test_team_task_board.py`  
**依赖：** T9

**步骤：**

1. 实现 pending/in_progress → completed/cancelled 的合法转换。
2. 所有依赖未 completed 时拒绝进入 in_progress；cancelled 依赖不算完成。
3. completed/cancelled 为终态；停止/失败成员不能新接 in_progress 任务。

**验证：** 合法转换、阻塞、取消依赖、终态回退和终态成员指派测试通过。

### T11：实现依赖 DAG 与原子更新

**文件：** `src/kcode/teams/task_board.py`、`tests/test_team_task_board.py`  
**依赖：** T10

**步骤：**

1. 在副本上同时应用 status、assignee、add/remove dependencies。
2. 对更新后的完整图运行环检测，覆盖两节点、三节点和多边同时新增。
3. 任一字段非法时保留原任务完全不变；空字符串 assignee 清除，未提供字段不修改。

**验证：** `uv run pytest tests/test_team_task_board.py -q`，尤其断言失败更新没有部分写入。

### T12：固定 Team 基础类型公共导出

**文件：** `src/kcode/teams/__init__.py`、`tests/test_team_manager.py`  
**依赖：** T1–T11

**步骤：**

1. 先导出上层接线所需的 Team 基础类型和错误；Manager 与工具注册入口在对应模块完成后追加。
2. 不从公共入口暴露邮箱内部 deque 或 TaskBoard 私有图算法。

**验证：** 公共 import 测试及 `uv run ruff check src/kcode/teams tests/test_team_*.py`。

## 第二阶段：扩展 TaskManager 的可复用生命周期

### T13：加入 TaskKind 与向后兼容字段

**文件：** `src/kcode/subagents/models.py`、`src/kcode/subagents/manager.py`、`tests/test_subagent_manager.py`  
**依赖：** 无

**步骤：**

1. 定义 `SUBAGENT` 与 `TEAM_MEMBER` 两类 TaskKind。
2. TaskRecord 增加 kind、retain_on_success、pinned、completion_callback 与 finalization details，默认值保持普通任务行为。
3. `launch` 新参数全部有兼容默认值，既有调用无需修改。

**验证：** 现有 `tests/test_subagent_manager.py` 先保持全绿，再通过新增默认字段测试。

### T14：实现类别受限的查询与控制

**文件：** `src/kcode/subagents/manager.py`、`src/kcode/subagents/service.py`、`tests/test_subagent_manager.py`  
**依赖：** T13

**步骤：**

1. summaries/get/stop 增加默认 `expected_kind=SUBAGENT` 边界。
2. 普通 SubAgentService 只能列出、读取和取消普通任务。
3. 内部 Team 调用显式使用 TEAM_MEMBER；kind 不符统一表现为不可见，而非泄露另一类任务信息。

**验证：** 普通列表不出现 Team 记录，猜中 Team task ID 也无法用普通接口操作。

### T15：实现 pinned 保留容量

**文件：** `src/kcode/subagents/manager.py`、`tests/test_subagent_manager.py`  
**依赖：** T13

**步骤：**

1. 淘汰只选择未 pinned 的 terminal 普通任务。
2. 提供无副作用容量预检供 Team spawn 在创建 Worktree 前调用。
3. idle Team 记录占 retained 容量、不占 running semaphore；容量满且无可淘汰项时明确失败。

**验证：** retained 边界、普通淘汰、pinned 不淘汰及无外部副作用测试通过。

### T16：扩展结构化 finalization

**文件：** `src/kcode/subagents/manager.py`、`src/kcode/subagents/service.py`、`src/kcode/worktrees/models.py`、`tests/test_subagent_manager.py`、`tests/test_worktree_subagents.py`  
**依赖：** T13

**步骤：**

1. `TaskFinalization` 增加默认空 details，TaskRecord 保存最终结构化快照。
2. `WorktreeFinalizationReport` 提供稳定 JSONValue mapping；现有 render 文本保持不变。
3. 普通隔离 SubAgent finalizer 同时写 suffix、warnings 和 details，不改变既有结果格式。

**验证：** 旧 Worktree 报告断言继续通过，新 details 字段在清理后仍含完整 review 信息。

### T17：实现成功后保留与完成回调

**文件：** `src/kcode/subagents/manager.py`、`tests/test_subagent_manager.py`  
**依赖：** T13、T16

**步骤：**

1. retain_on_success 的 completed 运行暂不消费 finalizer。
2. 执行顺序固定为确定状态、按规则 finalize、completion callback、普通通知。
3. callback 异常只追加脱敏 warning；不改变任务状态、不跳过之后的安全关闭。

**验证：** completed/failed/cancelled、callback 异常和调用顺序测试通过，finalizer 次数符合设计。

### T18：实现类别受限的 retained 续派

**文件：** `src/kcode/subagents/manager.py`、`tests/test_subagent_manager.py`  
**依赖：** T14、T17

**步骤：**

1. 实现 `resume_retained(task_id, prompt, expected_kind)`，只接受 completed、未 finalized 的同类记录。
2. 沿用原 ChildAgent、Conversation、Session、usage 和 finalizer，重置单轮结果/错误并重新进入并发槽。
3. 普通 `send_message` 固定 SUBAGENT；Team 记录、running、failed、cancelled 或 finalized 均拒绝。

**验证：** 同一 Conversation/usage 累积、kind 防绕过、并发上限和重复启动测试通过。

### T19：实现 wait、显式 finalize 与 release

**文件：** `src/kcode/subagents/manager.py`、`tests/test_subagent_manager.py`  
**依赖：** T17、T18

**步骤：**

1. `wait` 在有界时间内返回当前记录，不吞掉取消或 finalizer 状态。
2. `finalize_retained` 只对同类 terminal retained 记录恰好执行一次。
3. `release` 只移除已 terminal、非运行且已安全 finalization 的同类记录；不满足条件时拒绝。

**验证：** timeout、重复 finalize、错误 kind、运行中 release 和幂等清理测试通过。

### T20：收紧关闭时 pinned 任务收敛

**文件：** `src/kcode/subagents/manager.py`、`tests/test_subagent_manager.py`  
**依赖：** T15、T19

**步骤：**

1. close 取消全部运行记录，并对 idle retained 记录执行最终 finalizer。
2. 复用现有总计 5 秒窗口，超时后保留资源和 warning，不猜测删除。
3. completion callback、ApprovalBroker 和 finalizer 都不能因重复 close 执行两次。

**验证：** 快速/慢 callback、卡住 finalizer、idle pinned、审批等待与重复 close 测试通过。

### T21：运行普通 SubAgent 生命周期回归

**文件：** 既有 `tests/test_subagent_*.py`、`tests/test_worktree_subagents.py`  
**依赖：** T13–T20

**步骤：**

1. 验证普通前台、后台、自动转后台、Esc detach、取消、续派和通知。
2. 验证普通隔离任务仍在每轮结束后 finalize，shared 普通任务仍可续派。

**验证：** `uv run pytest tests/test_subagent_manager.py tests/test_subagent_tools.py tests/test_worktree_subagents.py -q`。

## 第三阶段：Runner、Factory 与工具安全边界

### T22：让 AgentRunner 注入 Team reminder

**文件：** `src/kcode/orchestration.py`、`tests/test_tool_orchestration.py`  
**依赖：** T8

**步骤：**

1. 定义 `TeamMessageSource` 协议和运行前可调用的 `bind_team_messages`。
2. 每次模型迭代在现有 Hook/task reminder 旁取出 Team 消息并构成单独 SystemReminderMessage。
3. 未绑定时 Prompt 和工具定义保持字节级/结构级兼容；绑定期间不启动额外 run。

**验证：** 下一迭代可见、只消费一次、Lead idle 不调用 Provider 和旧 Prompt 回归测试通过。

### T23：隐藏普通 Agent 的全部 Team 工具

**文件：** `src/kcode/subagents/filter.py`、`tests/test_subagent_filter.py`  
**依赖：** 无

**步骤：**

1. 把九个 `team_*` 名称加入普通定义式、Fork 和 Skill Fork 的控制边界。
2. Team 关闭、普通 SubAgent、Fork 和 Skill Fork 都不能继承任何成员协作工具。
3. 保持既有 agent/task_* 拒绝/隐藏语义不变。

**验证：** `uv run pytest tests/test_subagent_filter.py -q`。

### T24：构造 Team member Registry

**文件：** `src/kcode/subagents/filter.py`、`tests/test_subagent_filter.py`  
**依赖：** T23

**步骤：**

1. 先应用定义式角色 tools/disallowed_tools 和后台基础工具集合。
2. 再显式注册四个绑定成员工具：send_message、task_create/list/update。
3. 断言成员永远没有 agent、普通 task_* 和 Team create/spawn/status/stop/delete。

**验证：** general-purpose、只读角色、显式 MCP 和恶意白名单测试通过。

### T25：定义九个稳定工具 Schema

**文件：** `src/kcode/teams/tools.py`、`tests/test_team_tools.py`  
**依赖：** T1、T2

**步骤：**

1. 按 Spec 定义九个固定 Pydantic Args，全部 `extra=forbid`，并设置字符串/列表上限。
2. `team_status`、`team_task_list` 标为 read-only，其余标为 side-effect；工具始终 visible 且 Schema 不读当前 Team 状态。
3. sender 不出现在任何 Args；task_update 的未提供值与 `assignee=""` 语义可区分。

**验证：** 名称、必填/默认值、非法参数、ToolEffect、Schema 开关前后一致测试通过。

### T26：绑定并验证 Lead/Member Caller

**文件：** `src/kcode/teams/tools.py`、`tests/test_team_tools.py`  
**依赖：** T25

**步骤：**

1. 工具实例构造时绑定 Lead 或 member+team_id Caller。
2. execute 只把绑定 Caller 与模型参数传给 Manager，不接受或拼装 sender。
3. 用 fake Manager 证明 Lead 有九个工具，Member 只有四个，直接调用旧 Team 的成员工具会携带旧世代 ID。

**验证：** `uv run pytest tests/test_team_tools.py -q -k 'caller or member or schema'`。

### T27：增加 Team member Factory 入口

**文件：** `src/kcode/subagents/factory.py`、`tests/test_subagent_factory.py`、`tests/test_team_subagents.py`  
**依赖：** T22、T24、T26

**步骤：**

1. 复用 defined Agent 的 Provider、角色、权限收窄、max turns、SkillRuntime 与 HookSession 构造。
2. 注入成员 Registry、Team inbox source、`<team-context>` 与可选 Worktree Context。
3. shared 使用父 workspace_root但标明风险；worktree 复制 Context、清除 cancel_event/use_shell 并保持父 Context 不变。

**验证：** Provider/权限/Conversation 独立、Prompt、工具集合、Context 根和父对象不变测试通过。

### T28：注册稳定主 Agent Team 工具

**文件：** `src/kcode/teams/tools.py`、`tests/test_team_tools.py`  
**依赖：** T25、T26

**步骤：**

1. 提供 `register_team_tools(registry, manager)`，无条件注册 Lead 九个工具。
2. enabled=false、无 Team 和活跃 Team 三种状态的 Registry names/definitions 完全一致。
3. Plan Mode definitions 只暴露两个 read-only Team 工具，side-effect 操作不能借 Team 绕过 Plan。

**验证：** 全量 Registry Schema 快照、Plan Mode 与重复注册错误测试通过。

## 第四阶段：实现 TeamManager 生命周期

### T29：建立配置门禁、单 Team 与状态查询

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T1–T5、T9–T11、T13–T20

**步骤：**

1. 构造 Manager 的依赖与单一 asyncio.Lock，默认 active Team 为空。
2. enabled=false 时所有方法在外部副作用前返回 `teams_disabled`。
3. create 校验 Lead Caller、slug、非空 goal 和单 Team；status 返回 no_active_team 或结构化摘要。

**验证：** 默认关闭无 Task/Worktree 副作用，非法/重复创建及基本 status 测试通过。

### T30：验证 Team 世代与 Caller 权限

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T29

**步骤：**

1. Lead 可执行全部操作；Member 只允许消息和任务板四类操作。
2. Member 名称、状态与 team_id 必须匹配活跃 Team；模型不可伪造 lead 或其他成员。
3. 删除后新建同名 Team，旧工具实例统一返回 stale/invalid caller。

**验证：** 权限矩阵、伪造身份和旧世代测试通过。

### T31：实现 spawn 无副作用预检与 starting 预留

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T29、T30

**步骤：**

1. 预生成 task_id，锁内校验名称唯一、Team max_members、TaskManager running/retained 容量、SubAgent 开关、后台开关、Agent 定义和 isolation。
2. 只在全部预检通过后放入带 task_id 的 starting 占位；停止/失败成员名称仍不能复用。
3. 未知 role、满额、非法 isolation 和重复名不得创建 Agent 或 Worktree。

**验证：** 所有拒绝路径的 Factory/Worktree 调用次数为零，并发同名 spawn 仅一个预留成功。

### T32：实现默认 Worktree 成员启动

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`、`tests/test_team_subagents.py`  
**依赖：** T16–T20、T27、T31

**步骤：**

1. 锁外调用 `create_agent(task_id)`，构造隔离 Context、成员工具/inbox 与 completion callback。
2. 以 TEAM_MEMBER、background、retain_on_success、pinned 参数 launch，发布为 running。
3. 默认 Worktree unavailable、主目录 dirty 或检查失败原样拒绝，不回退 shared。

**验证：** 创建顺序、owner_id、TaskRecord flags、Context 根及三种 Worktree 拒绝测试通过。

### T33：实现显式 shared 成员启动

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`、`tests/test_team_subagents.py`  
**依赖：** T27、T31

**步骤：**

1. 仅当参数明确为 shared 时跳过 WorktreeManager。
2. 状态、Team Prompt 和启动结果都输出 isolation=shared 与冲突 warning。
3. 非 Git 项目可 shared 启动，但默认参数仍失败。

**验证：** 非 Git default/shared 对照、无 Git 调用和风险标记测试通过。

### T34：实现 spawn 失败安全回滚

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T32、T33

**步骤：**

1. 覆盖 Worktree 创建后 Factory 失败、TaskManager launch 失败、发布前 Team 状态变化。
2. 移除 starting 占位；已创建 Worktree 调同一 owner finalizer。
3. 错误返回清理或保留的结构化报告，不能留下可运行的无主成员。

**验证：** 三个故障注入点均无孤立 TaskRecord；无法证明安全的目录/分支保留可 review。

### T35：实现自然完成到 idle

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T32、T33

**步骤：**

1. completion callback 核对 Team 世代、member/task_id 后把成功成员 running → idle。
2. 保存 last_result/累计 Token；Worktree 使用只读 status，不执行 finalizer。
3. 把结果、Token、isolation 和 Worktree 状态作为成员消息投递 Lead inbox。

**验证：** 成员 idle 后 Conversation/Worktree/TaskRecord 仍存在、running slot 释放、Lead 收到完整报告。

### T36：实现失败与取消状态映射

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T35

**步骤：**

1. 未请求 stop 的 failed/cancelled 运行映射为 failed，不伪装 idle。
2. 确保 TaskManager 已执行 finalizer，保存结构化 Worktree 保留/清理报告。
3. failed 成员拒绝续派和新进行中任务，但仍留在名册和 status。

**验证：** Provider 异常、Agent failure、意外 cancel 与有成果/无成果矩阵测试通过。

### T37：实现 running 与 Lead 消息投递

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T7、T8、T30、T35

**步骤：**

1. sender 只取绑定 Caller；校验单播、lead 和原子广播目标。
2. running/starting 成员只入队，不重复 launch；Lead 只入队，不主动调用主 Runner。
3. 返回 delivered recipients 和实际 awakened 列表；stopped/failed 单播或广播失败不产生部分投递。

**验证：** 成员互发、发 Lead、广播、伪造 sender、终态目标和 Lead idle Provider 调用计数测试通过。

### T38：实现 idle 单次续派

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T18、T35、T37

**步骤：**

1. 锁内 idle → running 并设置 wake_scheduled，锁外调用 `resume_retained(...TEAM_MEMBER)`。
2. 续派 Prompt 只要求读取 reminder；消息从原 member inbox有序消费。
3. 并发多条消息只能触发一次 resume；失败恢复 idle、保留未读消息且不自动重试。

**验证：** 同一 ChildAgent/Conversation/Worktree、单次 launch、并发容量失败和未读保留测试通过。

### T39：关闭“最后一次迭代后到信”的竞态

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T38

**步骤：**

1. 构造成员已取 reminder、仍标 running、即将 completion 的可控测试屏障。
2. completion callback 发现未读消息时仅安排一个延后续派，不在当前 `_run` 内重入 TaskRecord。
3. stop/close 已开始时禁止该补续派，并保留消息/成果报告。

**验证：** 精确竞态测试重复运行通过，没有永久未读、双重 task 或停止后复活。

### T40：实现 team_stop

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T19、T36、T39

**步骤：**

1. running/starting 先置 stopping，锁外 cancel 并最多等待 5 秒；completion 根据 stop 请求映射 stopped。
2. idle 直接 finalize_retained，保存报告后 stopped；stopped/failed 重复 stop 幂等。
3. 超时保持 stopping，不强制删除；后续 callback 完成最终报告。

**验证：** starting/running/idle/terminal、快速取消、超时、有成果和 Git 检查失败测试通过。

### T41：实现 team_delete

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T40

**步骤：**

1. 任何 starting/running/stopping 成员存在时整体拒绝且不隐式 stop。
2. 对 idle 成员执行同一 finalizer，把所有 retained TaskRecord 安全 release。
3. 保存返回报告后清空邮箱、任务板、名册并移除 active Team；有成果 Worktree 永远保留。

**验证：** 运行时拒绝、全 idle、混合 stopped/failed、重复 delete 和清理失败矩阵测试通过。

### T42：实现应用退出 close

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T40、T41

**步骤：**

1. 设置关闭门禁，阻止新 create/spawn/message/resume。
2. 并发取消全部运行成员，共享一个 5 秒总等待窗口；idle 成员走相同 finalizer。
3. 清空内存协调状态，返回安全 warning；重复 close 幂等且不重复 finalizer。

**验证：** 三成员并发关闭耗时不按人数叠加，审批取消、无运行 Agent/Git 子进程、成果保留测试通过。

### T43：完成 Team 状态与 Catalog 更新

**文件：** `src/kcode/teams/manager.py`、`tests/test_team_manager.py`  
**依赖：** T29–T42

**步骤：**

1. status 输出 Team 名/目标、enabled、成员状态/isolation/tokens/Worktree、当前任务、pending inbox 和任务统计。
2. stopped/failed 成员保留最终报告；shared 明确没有 Worktree 且带 warning。
3. `set_catalog` 更新后续 spawn 的定义解析，不改变已运行成员角色。

**验证：** 完整状态快照、长结果保护、敏感值脱敏和 Catalog 前后对照测试通过。

### T44：把工具执行接到 TeamManager

**文件：** `src/kcode/teams/tools.py`、`src/kcode/teams/__init__.py`、`tests/test_team_tools.py`  
**依赖：** T25、T26、T29–T43

**步骤：**

1. 九个工具逐一调用对应 Manager 方法并把领域错误映射为稳定 ToolResult code。
2. enabled=false 全部返回 teams_disabled；无 Team 时除 create 外返回 no_active_team。
3. warnings、awakened、review details 与 32 KiB/truncated 标记保持结构化。
4. 从 `kcode.teams` 稳定导出 TeamManager、错误和 `register_team_tools`，上层不依赖内部邮箱/任务板实现。

**验证：** `uv run pytest tests/test_team_tools.py -q`，九个成功路径和错误矩阵全覆盖。

## 第五阶段：Slash Command、App 与 CLI 接线

### T45：注册 `/team` 命令

**文件：** `src/kcode/commands/models.py`、`src/kcode/commands/builtins.py`、`tests/test_team_commands.py`  
**依赖：** T44

**步骤：**

1. CommandHost 增加 status/stop/delete 三个异步方法。
2. 注册一个 REQUIRED `/team` 命令，严格接受 `status`、`stop <member>`、`delete`。
3. 缺参数、非法 slug、多余参数和未知子命令只显示完整用法；`/help team` 可发现。

**验证：** `uv run pytest tests/test_team_commands.py tests/test_commands.py -q`。

### T46：在 App 组装 TeamManager 与主工具

**文件：** `src/kcode/ui/app.py`、`tests/test_app.py`、`tests/test_team_subagents.py`  
**依赖：** T27、T28、T43、T44

**步骤：**

1. App 接收可选 TeamConfig/TeamManager 以便测试，默认安全构造一个 Manager。
2. 注入现有 Catalog、Factory、TaskManager、主 Runner、WorktreeManager 与敏感值。
3. 无条件注册 Lead 九工具，并给主 Runner 绑定 Lead inbox；关闭时 Schema 仍存在。

**验证：** App enabled on/off 构造、工具计数/Schema、非 Git 启动和无 Team 副作用测试通过。

### T47：同步 Agent Catalog 生命周期

**文件：** `src/kcode/ui/app.py`、`tests/test_app.py`  
**依赖：** T46

**步骤：**

1. App 在项目 Agent 信任、MCP 工具解析完成后，把同一个最终 Catalog 传给 SubAgentService 与 TeamManager。
2. 信任拒绝、MCP 失败或刷新 warning 不留下半更新 Team Catalog。
3. 已运行成员不因 Catalog 刷新改变 Registry/角色。

**验证：** 无 MCP、有 MCP、信任拒绝和刷新后 spawn 测试通过。

### T48：实现 App 的 `/team` Host 方法

**文件：** `src/kcode/ui/app.py`、`tests/test_team_commands.py`、`tests/test_app.py`  
**依赖：** T45、T46

**步骤：**

1. 三个 Host 方法复用 Lead Caller 调 Manager，不复制生命周期规则。
2. status 格式化目标、成员、Token、Worktree 和任务统计；stop/delete 输出保留路径与 warning。
3. 命令输出只进 TUI notice，不写 Conversation；旧 `/status` 内容保持不变。

**验证：** Fake Host 与 Textual Pilot 覆盖成功/失败，Conversation turn 数不变。

### T49：接入配置与启动 warning

**文件：** `src/kcode/cli.py`、`tests/test_cli.py`、`tests/test_team_config.py`  
**依赖：** T4、T46

**步骤：**

1. CLI 把 `config.teams` 传给 App，并把 team_warnings 合并进启动 warnings。
2. 项目级尝试开启时 TUI 可见 warning，但工具调用仍 teams_disabled。
3. 不含 Team 配置的旧 CLI 测试和启动参数保持兼容。

**验证：** `uv run pytest tests/test_team_config.py tests/test_cli.py tests/test_app.py -q -k 'team or warning or startup'`。

### T50：调整退出顺序并验证幂等

**文件：** `src/kcode/ui/app.py`、`tests/test_app.py`、`tests/test_team_integration.py`  
**依赖：** T42、T46

**步骤：**

1. `command_exit` 与 `on_unmount` 都先调用 TeamManager.close，再调用 TaskManager.close。
2. 保持 Hooks、Session、Memory、MCP 的原关闭顺序和重复调用防护。
3. TUI 已卸载时 warning 写 stderr，不尝试更新已销毁 widget。

**验证：** 显式 `/exit`、窗口卸载、重复退出和三成员运行中退出测试通过。

## 第六阶段：安全与端到端集成

### T51：验证成员权限与后台审批

**文件：** `tests/test_team_subagents.py`、既有权限/审批测试  
**依赖：** T27、T32、T46

**步骤：**

1. Team 成员仍受父权限上限、Plan Mode、黑名单、项目规则和 Hook 约束。
2. 后台写入/命令进入同一 ApprovalBroker，显示 task 与 Team 成员来源。
3. Registry 过滤和 Manager Caller 校验分别测试，任何一层被直接绕过仍拒绝。

**验证：** `uv run pytest tests/test_team_subagents.py tests/test_subagent_approval.py tests/test_permissions.py -q`。

### T52：验证两个成员的真实 Worktree 隔离

**文件：** `tests/test_team_integration.py`  
**依赖：** T32、T46

**步骤：**

1. 在真实临时 Git 仓库启动两个默认成员，确认路径、分支和 owner 全部不同。
2. 两人同时修改同名文件并运行命令，成果只落在各自 Worktree。
3. 主目录文件、分支、cwd、Context 与另一成员绝对路径访问保持不变/被拒绝。

**验证：** `uv run pytest tests/test_team_integration.py -q -k worktree_isolation`。

### T53：验证任务 DAG 与消息协作端到端

**文件：** `tests/test_team_integration.py`  
**依赖：** T11、T37–T39、T44

**步骤：**

1. Lead 创建有依赖的两个任务并指派不同成员，阻塞任务不能提前 in_progress。
2. 成员完成上游、发消息、下游解除阻塞；running/idle 两种消息路径都可观察。
3. idle 续派沿用同一 Conversation/Worktree，Lead 空闲时消息保留到下一次用户请求。

**验证：** `uv run pytest tests/test_team_integration.py -q -k collaboration`。

### T54：验证 stop/delete/exit 的成果保护

**文件：** `tests/test_team_integration.py`  
**依赖：** T40–T42、T50

**步骤：**

1. 覆盖 clean/no commit、dirty、new commit 和 Git status 失败四类成员结局。
2. stop/delete/exit 只清理可证明无成果的自动目录和分支；其余全部保留。
3. 报告包含 path、branch、base、HEAD、dirty、kept、reason，可在 App 退出后人工 review。

**验证：** `uv run pytest tests/test_team_integration.py -q -k 'stop or delete or exit or result'`。

### T55：验证费用、容量与 Schema 稳定

**文件：** `tests/test_team_integration.py`、`tests/test_team_tools.py`、`tests/test_team_config.py`  
**依赖：** T31、T38、T44、T49

**步骤：**

1. 默认关闭和项目开启均不调用 Provider/Factory/Worktree；用户开启后最多三个成员。
2. Team max、全局 running、retained 三类上限分别测试，拒绝后资源状态一致。
3. enabled、无 Team、活跃 Team 和 delete 后九个主 Tool Schema 保持一致；Lead idle 不自动调用模型。

**验证：** 三个测试文件的 `cost/capacity/schema` 场景全部通过。

### T56：运行 M2 定向回归

**文件：** 全部 Team 测试及受影响既有测试  
**依赖：** T1–T55

**步骤：**

1. 运行所有 `tests/test_team_*.py`。
2. 运行 SubAgent、Worktree、Orchestration、Permission、Hook、Command、Config、CLI 与 App 受影响测试。
3. 任何失败先跑最小测试定位并修复，再重跑整组。

**验证：** 所有定向命令退出码为 0，无未解释 warning 或跳过项。

## 第七阶段：文档、版本与质量门禁

### T57：更新配置示例与 README

**文件：** `config.example.yaml`、`README.md`  
**依赖：** T49、T55

**步骤：**

1. 示例加入默认关闭、最多三个成员的用户级 Team 配置。
2. README 说明九个工具、三个 `/team` 子命令、默认 Worktree、显式 shared、idle 续派和并行 Token 费用。
3. 明确 in-process、重启不恢复、不自动唤醒 Lead、不自动 commit/merge 和成果 review 方法。

**验证：** 文档命令/默认值与实际 Schema 对照一致，示例 YAML 可被 `load_config` 解析。

### T58：完成 M2 与版本级 Checklist

**文件：** `docs/agent-teams/checklist.md`、`docs/releases/0.8.0-checklist.md`、`docs/worktrees/checklist.md`  
**依赖：** 用户在下一阶段批准两份新 Checklist；T51–T57

**步骤：**

1. 按已批准的 Agent Team Checklist 逐项记录可观测证据。
2. 复核 Worktree M1 已验收项未回归，并在版本级 Checklist 汇总 M1+M2。
3. 不把测试尚未覆盖或只能推测的行为标记为完成。

**验证：** AC1–AC13 每条至少有一个测试/命令/真实 Git 场景证据，版本 Checklist 无悬空项。

### T59：统一升级到 0.8.0

**文件：** `src/kcode/__init__.py`、`pyproject.toml`、README/帮助相关版本断言  
**依赖：** T56–T58

**步骤：**

1. 仅在 M1、M2 Checklist 都通过后，把运行时与 package metadata 同时改为 `0.8.0`。
2. 更新 README、配置示例、帮助命令数量和测试中的版本/工具数量断言。
3. 全仓搜索旧发布版本；保留历史文档中作为基线叙述的 `0.7.0`，修正所有当前版本引用。

**验证：** `uv run kcode --version`、Python `kcode.__version__`、`pyproject.toml` 和 wheel metadata 均为 0.8.0。

### T60：运行全仓质量检查

**文件：** 全仓  
**依赖：** T59

**步骤：**

1. 运行 `uv run pytest`。
2. 运行 `uv run ruff check .` 与 `uv run ruff format --check src tests`。
3. 构建 wheel，检查 `kcode.teams`、内置 Agent/Skill/Hook 资源、README 和 0.8.0 metadata。
4. 运行 `git diff --check`，检查工作树只含本版本有意改动。

**验证：** 所有命令退出码为 0；完整测试数量、skip 与 wheel 内容记录到版本 Checklist。

### T61：完成最终人工 E2E

**文件：** 不新增实现文件，只记录验收证据  
**依赖：** T60

**步骤：**

1. 在干净真实仓库启用 Team，创建一个 Team 和两个默认 Worktree 成员。
2. 创建依赖任务、成员互发消息、观察 idle 续派、后台审批和 Lead 下次请求收信。
3. 让两个成员产生不同成果，stop/delete 后确认成果仍可 review，主目录未被自动合并。
4. 退出 Kcode，确认无运行 Agent/Git 子进程；重新启动后 Team 不恢复，但成果 Worktree 仍存在。

**验证：** 用户可见输出与两份 Checklist 一致，无自动 Git 收敛或数据丢失。

## 执行顺序

```text
T1–T12  领域模型 / 配置 / 邮箱 / 任务板
   ├──────────────→ T22–T28  Runner / Factory / 工具边界
   └→ T13–T21  TaskManager 生命周期 ───────────────┐
                                                   ▼
                                      T29–T44  TeamManager
                                                   ▼
                                      T45–T50  Command / App / CLI
                                                   ▼
                                      T51–T56  安全与集成测试
                                                   ▼
                                      T57–T61  文档 / 0.8.0 / 验收
```

具有写依赖的任务不得并行。每个实现任务必须先看到对应测试失败，再完成最小实现并让该测试通过；不得用最终全仓测试替代当前任务的局部验证。

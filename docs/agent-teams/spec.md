# Kcode Agent Team MVP Spec

> 状态：已批准。基于已验收的 Worktree M1 与现有 SubAgent/TaskManager；仅实现单进程内存后端。

## 背景

Kcode 已支持普通 SubAgent 委派，以及可选的 Git Worktree 文件系统隔离。普通 SubAgent 仍采用星型协作：每个子 Agent 只向主 Agent 返回结果，子 Agent 之间不能共享任务状态或直接交换消息。对于多个相对独立、但需要同步进展和依赖关系的工作，主 Agent既要决策又要人工中转，容易形成瓶颈。

Agent Team 在现有 SubAgent 上增加一个有明确 Lead、成员名册、共享任务板和点对点消息的小组。首版只在当前 Kcode 进程中存在，不提供 tmux、跨进程持久化或自动合并。Team 成员默认使用已验证的 Worktree 隔离；Lead 负责拆分、协调和在用户批准后人工收敛成果。

Team 可能并行产生多路模型 Token 费用，因此默认关闭，只能由用户级配置明确开启。

## 目标

- 让 Lead 在一个 Kcode 进程内创建并管理一个长期存在的 Agent Team。
- 让最多三个命名成员在独立 Conversation 中并行工作，并能直接交换消息。
- 用共享任务板表达负责人、进度和无环依赖，避免重复工作和提前启动被阻塞任务。
- 默认用 Worktree 隔离成员文件系统；任何结束清理继续遵守“无法证明安全就保留”的原则。
- 复用现有 SubAgent、TaskManager、权限审批、取消和 Token 统计能力，不建立第二套 Agent Loop。
- 保持 Team 关闭时的工具 Schema、普通 SubAgent、旧配置和会话行为兼容。

## 功能需求

### F1：显式配置与费用安全

- 新增用户级 Team 配置，包含 `enabled` 和 `max_members`；默认值为 `false` 和 `3`。
- `max_members` 必须在安全的有限范围内，默认最多同时拥有三个 Team 成员。
- 项目级 Team 配置整段忽略并产生 warning，不能替用户开启 Team、提高成员上限或触发模型费用。
- Team 控制工具在开启和关闭时都保持注册及 Schema 稳定；关闭时任何 Team 操作返回结构化 `teams_disabled`，不创建 Agent 或 Worktree。
- README 和配置示例必须明确说明：启用 Team 后，成员启动和 idle 续派可能产生并行模型费用。

### F2：单 Team 与身份规则

- 同一 Kcode 进程同一时刻只允许一个活跃 Team。
- Team 创建需要安全名称和非空目标；创建成功后当前主 Agent 成为 Lead。
- Team 名称和成员名称均为最长 64 字符的单段小写 slug；拒绝空值、点段、空白、斜杠和路径遍历。
- 成员名称在该 Team 的整个生命周期中唯一；成员停止或失败后也不能用同名创建另一个身份，避免旧消息和任务误投。
- Team、成员名册、任务板和消息只存在于当前进程内存；Kcode 重启后不恢复，也不扫描旧 Team 状态。

### F3：稳定的 Lead 工具接口

主 Agent 始终注册以下稳定工具：

- `team_create(name, goal)`
- `team_spawn(name, prompt, subagent_type="general-purpose", isolation="worktree")`
- `team_status()`
- `team_stop(name)`
- `team_delete()`
- `team_send_message(to, message)`
- `team_task_create(title, description, assignee, blocked_by)`
- `team_task_list(status)`
- `team_task_update(task_id, status, assignee, add_blocked_by, remove_blocked_by)`

工具参数模型不能依赖当前 Team 名称、成员列表或任务内容动态变化。没有活跃 Team 时，除 `team_create` 外的工具返回结构化 `no_active_team`。

### F4：用户干预命令

提供以下本地 Slash Command：

- `/team status`：查看 Team 目标、成员状态、Token、Worktree 与任务统计。
- `/team stop <member>`：请求停止指定成员。
- `/team delete`：删除当前可安全删除的 Team 协调状态。

Slash Command 不进入 Conversation，也不能绕过与同名工具一致的生命周期和安全清理规则。`/help` 必须能发现命令及子命令用法。

### F5：成员创建与容量

- Team 成员复用现有定义式 SubAgent 构造、Provider 选择、角色最大轮次和父子权限上限。
- 每个成员拥有独立名称、Conversation、Session 状态、取消状态、Token 统计和消息收件箱。
- 成员默认 `isolation="worktree"`；创建发生在 Agent 执行前，并复用 M1 的路径、脏主目录拒绝、Context 隔离和所有权检查。
- 非 Git 项目或只读角色可以显式选择 `shared`；默认 Worktree 不可用时不得静默回退为 shared。
- Team 成员受 `teams.max_members`、现有 SubAgent 全局运行并发上限和任务保留上限共同约束；任一上限达到时在创建 Agent/Worktree 前或安全回滚后拒绝。
- 创建中任一步骤失败都不留下可运行的无主成员；已创建 Worktree 必须走同一安全检查，并把清理或保留报告返回 Lead。

### F6：成员状态与自然 idle

- 成员状态至少包括 `starting`、`running`、`idle`、`stopping`、`stopped` 和 `failed`。
- 首次任务自然完成后成员进入 `idle`，不自动删除 Conversation 或 Worktree；结果、累计 Token 和当前 Worktree 状态报告投递给 Lead。
- idle 成员收到新消息时沿用原 Conversation、角色、权限上限和同一 Worktree异步续派，并进入 `running`。
- running 成员不能被重复启动；stopped 或 failed 成员拒绝续派。
- 成员的新一轮失败不会伪装为 idle；状态、错误摘要和 Worktree review 信息必须对 Lead 可见。

### F7：任务运行时分类与兼容性

- Team 成员复用现有 TaskManager 的并发、审批、取消、Token 汇总、结果脱敏和大小限制。
- TaskManager 必须能区分普通 SubAgent 与 Team 成员，并在完成后通知所属 Team，而不是把 Team 生命周期硬编码进普通任务逻辑。
- 普通 SubAgent 继续出现在现有 `task_list/get/stop/send_message` 中，行为不变。
- Team 成员不出现在普通 `task_*` 列表，也不能被普通 `task_send_message` 续派；只由 TeamManager 通过成员名称管理。
- Team 成员 idle 后仍占用 Team 成员名额，但不占用“正在运行”的并发槽位。

### F8：成员工具边界

- Lead 可以使用全部 Team 控制工具；Team 成员不能创建、扩容、停止或删除 Team。
- Team 成员不能调用普通 `agent` 和普通 `task_*` 工具，不能创建孙 Agent 或绕过 TeamManager。
- Team 成员只获得角色允许的基础工作工具、既有安全工具，以及 `team_send_message`、`team_task_create`、`team_task_list`、`team_task_update` 四类协作工具。
- 关闭 Team、普通 SubAgent 或 Skill Fork 不得看到成员协作工具。
- 工具过滤只是第一层；每个 Team 工具仍必须验证绑定调用者身份和活跃 Team，防止绕过 Registry 直接调用。

### F9：共享任务板

- Team 任务包含唯一任务 ID、标题、描述、状态、负责人和依赖集合。
- 状态固定为 `pending`、`in_progress`、`completed`、`cancelled`。
- 负责人只能是 Lead、当前 Team 成员或空值；停止/失败成员不能接收新的进行中任务。
- 创建和更新依赖时，所有任务必须存在，不能依赖自身，最终依赖图必须无环；非法更新不产生部分变化。
- 只有所有依赖均为 `completed` 时，任务才能从 pending 进入 in_progress。cancelled 依赖不视为完成，必须显式移除依赖或取消下游任务。
- completed 和 cancelled 是终态；终态任务不能重新进入 pending/in_progress。
- 任务列表可以按状态过滤，并明确显示依赖是否已满足。

### F10：不可伪造的消息身份

- 每个 Team 工具实例在创建时绑定调用者身份；发送者不作为模型可填写参数，成员不能冒充 Lead 或其他成员。
- `to` 支持成员名称、`lead` 和 `*`。单播目标必须存在；广播投递给除发送者外的所有 Team 参与者。
- 消息按收件人保持发送顺序，并包含发送者、接收者、正文和创建时间。
- 消息正文和汇总受长度上限、敏感值脱敏与 Prompt 注入边界保护；消息内容始终作为不可信协作数据呈现，不作为更高优先级系统指令。
- 对 stopped/failed 成员发送消息返回明确失败，不静默丢弃，也不重新创建成员。

### F11：消息投递与续派

- running 成员收到的消息在其下一次模型迭代前以结构化 `<team-messages>` reminder 注入，并在成功取出后标记已读。
- idle 成员收到单播或广播消息时，由消息触发异步续派；同一时刻多条消息合并为一次有序输入，避免重复启动。
- Team 成员向 Lead 发送的消息：Lead 正在运行时在下一次模型调用前看到；Lead 空闲时保留到下一次用户请求，不自动唤醒模型、不自动产生费用。
- 成员自然 idle 时的结果通知也走 Lead 的同一收件通道。
- 消息触发成员续派可能产生模型费用；操作结果必须返回实际唤醒的成员列表。

### F12：权限、审批与文件隔离

- Team 不能放宽父子权限上限、Plan Mode、危险命令黑名单、项目规则、Hook 或后台审批。
- Team 成员的写入和命令仍通过现有审批队列，显示 Team 与成员来源；Team 不得设置“无人值守自动批准”。
- Worktree 成员的文件、搜索、命令 cwd、环境与权限沙箱都以成员目录为根；主目录和其他成员 Worktree 的绝对路径被拒绝。
- shared 成员明确共享主目录并承担并发写冲突风险；状态和 Team 报告必须标出 isolation，Lead 不得把 shared 伪装成已隔离。

### F13：停止、删除与应用退出

- `team_stop(name)` 对 running/starting 成员请求取消并等待有界结束；对 idle 成员直接进入 stopped；重复停止返回当前终态。
- stop 后检查 Worktree：确认无未提交修改且 HEAD 等于基线时安全清理自动目录和临时分支；有成果或检查失败时保留并报告 review 路径。
- `team_delete()` 在仍有 starting/running/stopping 成员时拒绝，不隐式强制取消。
- 无运行成员时 delete 将 idle 成员转为 stopped，删除内存任务板、消息和名册；只清理可证明无成果的临时 Worktree，其余成果全部保留。
- Kcode 退出时取消运行成员、执行同一有界成果检查并清空内存协调状态；退出不得强制删除有成果 Worktree，也不得留下运行中的 Agent/Git 子进程或审批请求。

### F14：Lead 收敛边界

- Team 不自动 commit、merge、cherry-pick、rebase 或解决冲突。
- 成员完成报告必须给出路径、分支、base、HEAD、dirty 和保留状态，便于 Lead 与用户 review。
- Lead 只有在用户批准且主目录干净时，才能通过现有 `run_command` 人工执行 Git 收敛；仍受危险命令、权限审批和 Hook 约束。
- 收敛失败不能触发强制删除；Worktree 与分支保留，Lead 向用户报告冲突和可恢复位置。

### F15：用户可见状态与版本集成

- `team_status` 和 `/team status` 显示 Team 名称、目标、启用状态、成员状态/隔离/任务/Token/Worktree 摘要，以及任务板统计。
- Team 结果、消息和 Worktree 报告经过现有敏感值脱敏与 32 KiB 结果限制；Worktree review 尾部信息不能被长正文截掉。
- Team 关闭或无活跃 Team 时，普通 `/status`、会话存档和主 Agent cwd 行为不变。
- M2 验收完成后，Kcode 版本统一更新为 0.8.0，README、配置示例、帮助命令数量和 wheel metadata 保持一致。

## 非功能需求

- **N1 费用默认安全：**Team 默认关闭；项目内容不能开启；Lead 空闲时消息不自动触发模型调用。
- **N2 单进程确定性：**Team 状态只由当前事件循环管理，不依赖 tmux、文件邮箱或后台扫描器。
- **N3 身份完整性：**发送者、Team 和成员身份由运行时绑定，不能由模型参数伪造。
- **N4 并发一致性：**成员状态、消息消费、任务依赖更新和 stop/delete 使用明确同步边界，不重复续派或部分更新。
- **N5 安全默认值：**任何无法证明可清理的 Worktree 都必须保留；任何无法确认身份或任务图合法性的操作都拒绝。
- **N6 Schema 稳定：**Team 开关、成员数量和活跃状态不改变主 Agent 稳定工具 Schema。
- **N7 可测试性：**协作状态可使用内存替身和可预测 Provider 测试；Worktree 结合真实临时 Git 仓库集成测试。
- **N8 向后兼容：**现有普通 SubAgent、Skill Fork、Hook、MCP、权限、历史、长期记忆和 TUI 测试保持通过。

## 不做的事

- tmux、iTerm2、独立终端窗格或跨进程后端。
- 文件邮箱、Team 磁盘持久化、跨会话恢复或 Kcode 重启后续派。
- 多个并存 Team、嵌套 Team、成员创建孙 Agent或任意深度任务树。
- Coordinator Mode、Lead 工具剥夺或自动 Research/Synthesis/Verification 流程。
- Plan 提交—Lead 审批协议或成员权限模式自动切换。
- Lead 或 idle 成员的定时轮询、自动唤醒、无用户请求的自主模型调用。
- 自动提交、自动合并、自动 cherry-pick/rebase、冲突自动解决或强制删除。
- 自动复制 `.env`、本地配置、ignored 文件或链接依赖目录。
- Team 跨机器分布式运行、网络消息、优先级、deadline 或复杂调度策略。
- 修改普通 `task_*` 工具去混合显示 Team 成员。

## 验收标准

- **AC1（F1）：**默认配置下所有 Team 工具返回 `teams_disabled` 且无模型/Worktree副作用；只有用户配置能开启，项目配置被忽略并 warning。
- **AC2（F2）：**同进程只能创建一个安全命名 Team；重复 Team、非法名称和成员重名被拒绝；重启不恢复 Team。
- **AC3（F3–F4）：**九个 `team_*` 工具 Schema 稳定，三个 `/team` 子命令可从帮助发现；无 Team 与非法参数返回结构化错误。
- **AC4（F5）：**最多三个命名成员受 Team 和全局并发上限约束；默认获得独立 Worktree，非 Git 只能显式 shared，创建失败无无主资源。
- **AC5（F6–F7）：**成员完成后进入 idle、结果/Token/Worktree报告到达 Lead；idle 消息沿用同一 Conversation 与 Worktree续派；普通 task 列表不出现 Team 成员。
- **AC6（F8）：**Team 成员看不到 agent、普通 task_* 与生命周期控制工具，只能使用角色基础工具和四类协作工具；直接绕过调用仍被身份校验拒绝。
- **AC7（F9）：**任务创建、过滤、指派和状态转换正确；不存在任务、自依赖、依赖环、终态回退及未解除阻塞的 in_progress 全部被拒绝且无部分更新。
- **AC8（F10–F11）：**发送者不能伪造；成员/lead/广播寻址正确；running 在下一轮收信、idle 合并消息后只续派一次、Lead 空闲不自动唤醒、stopped/failed 不续派。
- **AC9（F12）：**后台审批仍可用；Team 不能绕过父权限、Plan Mode、黑名单、Hook 或沙箱；两个成员修改同名文件落在不同 Worktree，主目录不变。
- **AC10（F13）：**stop、delete 和应用退出对无成果 Worktree安全清理，对有修改、新 commit 或检查失败的 Worktree/分支全部保留并报告；运行成员存在时 delete 拒绝。
- **AC11（F14）：**系统不产生自动 Git 收敛动作；成果报告足以人工 review，用户未批准或主目录 dirty 时 Lead 不执行合并。
- **AC12（F15）：**Team 状态、Token、任务和 Worktree 可观察；敏感值与长结果受到保护；版本、README、示例、帮助和 wheel 一致更新为 0.8.0。
- **AC13（N1–N8）：**全仓测试、Ruff、格式、wheel 资源和 `git diff --check` 全部通过，Team 关闭时旧功能与工具 Schema 保持兼容。

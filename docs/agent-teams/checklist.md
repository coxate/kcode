# Kcode Agent Team MVP Checklist

> 状态：已批准。每一项都通过运行代码或观察真实行为验证，不以阅读实现代替验收；没有证据的项目保持未勾选。

## 配置、费用与稳定入口

- [ ] **AC1 默认关闭：**使用不含 `teams` 的旧用户配置启动，九个 `team_*` 工具仍可发现，但每个操作都返回 `teams_disabled`，Provider、SubAgentFactory、TaskManager 和 WorktreeManager 的调用计数均不增加。（验证：配置/工具集成测试比较调用前后计数）
- [ ] **AC1 用户显式开启：**只有用户级 `teams.enabled: true` 能开启 Team，`max_members` 只接受 1～3；默认值分别为 false 和 3。（验证：参数化加载用户配置并观察 App 中的有效配置）
- [ ] **AC1 项目不能开启：**项目配置中的整个 `teams` 段被忽略并显示 warning，不能开启 Team、改变成员上限或触发模型/Worktree 副作用。（验证：仅项目开启和用户关闭+项目开启两种启动场景）
- [ ] **AC1 费用提示：**配置示例和 README 明确说明 spawn 与 idle 续派可能产生并行 Token 费用，Lead 空闲时收信不会自动调用模型。（验证：文档对照与 Provider 调用计数测试）
- [ ] **AC2 单 Team：**创建一个 Team 后，第二次 create 被拒绝；delete 成功后才可创建下一 Team，进程重启不会恢复旧 Team。（验证：同进程 create/create/delete/create 与重启场景）
- [ ] **AC2 安全身份：**Team 名和成员名的空值、点段、大小写、空白、两种斜杠、遍历及超过 64 字符全部被拒绝；停止/失败成员的名称在当前 Team 内仍不能复用。（验证：名称矩阵及 stop/failed 后重名 spawn）
- [ ] **AC3 稳定 Schema：**enabled=false、enabled=true 但无 Team、活跃 Team、成员变化和 delete 后，主 Agent 的九个工具名称、参数 Schema 与默认值完全一致。（验证：五个状态的 Registry definitions 快照对比）
- [ ] **AC3 结构化错误：**无活跃 Team 时除 create 外统一返回 `no_active_team`；非法成员、任务、依赖和生命周期操作返回稳定 code，不泄漏 traceback、密钥或内部绝对路径以外的信息。（验证：九工具错误矩阵与敏感值样本）
- [ ] **AC4 命令发现：**`/help` 可看到 `/team`，`/help team` 展示 `status|stop <member>|delete`；缺参、多参和未知子命令只显示用法且不改变 Team。（验证：Command Dispatcher 与 Textual Pilot）
- [ ] **AC4 命令不进历史：**三个 `/team` 子命令只产生本地 notice，不增加 Conversation turn，也不能绕过同名工具的 stop/delete 安全规则。（验证：命令前后历史快照及生命周期结果对照）

## 成员创建、运行与普通任务兼容

- [ ] **AC4 默认 Worktree：**用户未传 isolation 时，成员执行前先得到独立 Worktree；路径、分支、base 和 owner 可观察，成员文件/命令 cwd 都以该路径为根。（验证：真实临时 Git 仓库 spawn 并交叉检查 Git 与 ToolContext）
- [ ] **AC4 dirty 拒绝：**主目录存在 tracked 或 untracked 修改，或 Git 状态检查失败时，默认成员在启动模型前被拒绝，且没有新 TaskRecord、目录、分支或元数据。（验证：三个场景比较前后 Provider/Git/Task 状态）
- [ ] **AC4 不静默降级：**非 Git 项目使用默认 worktree 明确失败；只有显式 `isolation="shared"` 才能启动，状态和 Prompt 均显示 shared 冲突风险。（验证：同一非 Git 项目的 default/shared 对照）
- [ ] **AC4 三重容量：**Team 成员数、全局正在运行数、TaskManager retained 容量任一达到上限时 spawn 被拒绝；拒绝前或安全回滚后没有无主 Worktree/TaskRecord。（验证：分别填满三个上限并对账资源）
- [ ] **AC4 启动回滚：**Worktree 创建后 Factory 失败、TaskManager launch 失败或发布竞态时，成员不会变成可运行孤儿；无成果安全清理，有成果或检查失败保留并报告。（验证：三个故障点注入和 Git 对账）
- [ ] **AC5 自然 idle：**成员成功完成后状态变为 idle，结果、累计 Token、isolation 与 Worktree 状态进入 Lead 收件箱；原 Conversation、Session、TaskRecord 和 Worktree仍存在。（验证：完成一次可预测 Provider 任务后检查状态和对象身份）
- [ ] **AC5 idle 不占运行槽：**三个成员都 idle 时仍占三个成员名额和 retained 容量，但 TaskManager running 数为零，普通 SubAgent可使用释放出的运行槽。（验证：状态计数与再启动普通 SubAgent）
- [ ] **AC5 失败可见：**Provider/Agent 异常或非 stop 取消后成员为 failed 而非 idle，错误摘要、Token 与最终 Worktree report 对 Lead 可见，且成员不能续派。（验证：三类失败和后续消息尝试）
- [ ] **AC5 普通任务不混入：**`task_list/get/stop/send_message` 只显示和操作普通 SubAgent；即使猜中 Team task ID，也不能查询、停止或续派 Team 成员。（验证：同时运行两类任务并交叉调用）
- [ ] **AC5 普通 SubAgent 回归：**前台、显式后台、自动转后台、Esc detach、取消、shared 续派和隔离 finalizer 的旧行为不变。（验证：既有 SubAgent/Worktree 定向测试全部通过）

## 工具权限与不可伪造身份

- [ ] **AC6 Lead 工具边界：**Lead 可见全部九个工具；Team 成员只可见基础角色工具及 send_message、task_create/list/update 四类协作工具。（验证：主/成员 Registry definitions 对比）
- [ ] **AC6 禁止嵌套控制：**Team 成员不可见 `agent`、普通 `task_*`、team create/spawn/status/stop/delete；角色白名单或 MCP 列表也不能重新引入它们。（验证：general-purpose、只读、恶意白名单和显式 MCP 角色）
- [ ] **AC6 普通 Agent 无协作工具：**Team 关闭、普通定义式 SubAgent、Fork 和 Skill Fork 都看不到任何 `team_*` 成员工具。（验证：四类 Registry 快照）
- [ ] **AC6 双层身份校验：**绕过 Registry 直接执行成员工具时，绑定 member/team generation 不匹配仍被拒绝；删除后新建同名 Team，旧成员工具不能连接新 Team。（验证：直接 Tool.execute 与世代重建测试）
- [ ] **AC6 Plan/权限上限：**Plan Mode 只能调用 team_status 与 team_task_list，不能 create/spawn/message/变更任务；成员不能放宽父权限、黑名单、项目规则或 Hook。（验证：Plan tool definitions 及权限矩阵）
- [ ] **AC9 后台审批：**Team 成员的写入和命令进入现有审批队列，界面显示 task/成员来源；批准、拒绝、取消和退出都能继续，Team 没有无人值守自动批准。（验证：Textual Pilot/ApprovalBroker 集成场景）

## 共享任务板

- [ ] **AC7 创建与查询：**任务具有唯一 ID、标题、描述、pending 状态、可选负责人和依赖；可按四种状态过滤，并显示 ready 与未完成依赖。（验证：任务工具创建后逐种过滤）
- [ ] **AC7 负责人校验：**负责人只能为空、lead 或当前 Team 成员；未知成员被拒绝，停止/失败成员不能接收新的 in_progress 任务。（验证：负责人矩阵和终态成员场景）
- [ ] **AC7 阻塞规则：**只有全部依赖 completed 后任务才能进入 in_progress；cancelled 依赖不算完成，必须移除依赖或取消下游。（验证：两层依赖的状态转换）
- [ ] **AC7 DAG 安全：**不存在任务、自依赖、二/三节点环和多边组合环都被拒绝；错误更新后原任务的状态、负责人和依赖全部不变。（验证：更新前后完整快照比较）
- [ ] **AC7 终态不可回退：**completed/cancelled 任务不能恢复 pending/in_progress；pending/in_progress 到终态的合法转换可观察。（验证：完整状态机参数测试）
- [ ] **AC7 任务不暗中调度：**创建、指派、解除依赖和更新状态本身不会唤醒成员或调用 Provider，只有 spawn 或发给 idle 成员的消息才可能产生费用。（验证：任务操作前后 Provider/TaskManager 调用计数）

## 消息、投递与续派

- [ ] **AC8 发送者不可伪造：**九个 Schema 中没有 sender 参数；Lead 和成员发出的消息均显示运行时绑定身份，模型正文不能伪装消息元数据。（验证：Schema 检查及含伪造 sender/XML 的正文）
- [ ] **AC8 单播与顺序：**成员、lead 寻址正确，同一收件人按发送顺序读取；未知目标返回错误且不产生部分投递。（验证：交错发送后按收件箱 sequence 对比）
- [ ] **AC8 原子广播：**`to="*"` 投递给除发送者外所有参与者；若包含 stopped/failed 目标则整次失败，任何有效收件人也不会收到半次广播。（验证：正常与终态成员广播前后 pending 对比）
- [ ] **AC8 running 投递：**running 成员收到消息时不启动第二个任务，在下一次模型迭代前看到一个有序 `<team-messages>` reminder。（验证：可控多迭代 Provider 与 launch 计数）
- [ ] **AC8 idle 单次续派：**idle 成员收到多条并发消息只异步续派一次，沿用完全相同的 ChildAgent、Conversation、Session 和 Worktree，累计 Token 持续增加。（验证：并发发送、对象 identity 与 usage 对比）
- [ ] **AC8 续派失败保信：**全局并发满、Manager 关闭或 launch 失败时成员恢复 idle，消息保持未读，不自动重试和重复收费。（验证：故障注入后 pending/Provider 调用计数）
- [ ] **AC8 完成竞态：**消息在成员最后一次 reminder 读取后、completion 前到达时，完成回调只补一次延后续派；消息不永久滞留、不双重启动，stop/close 后不复活。（验证：带事件屏障的竞态测试重复运行）
- [ ] **AC8 Lead 不自动唤醒：**成员给 Lead 的消息和 idle 结果在 Lead 运行时于下一迭代可见；Lead 空闲时一直保留到下一次用户请求，期间 Provider 调用数不变。（验证：running/idle 两种 Lead 状态）
- [ ] **AC8 Prompt 注入边界：**消息正文被标为不可信协作数据，伪 System 指令、XML 边界和敏感值不能改变权限/System Prompt，消息和结果均受长度限制。（验证：恶意正文、敏感值和超长 UTF-8 样本）
- [ ] **AC8 终态拒绝：**stopped/failed 成员的单播和续派返回明确错误，不重新创建成员、不丢失已有报告。（验证：两种终态后的发送和状态快照）

## Worktree、审批与成果保护

- [ ] **AC9 Context 全隔离：**两个默认成员的读取、写入、编辑、搜索、命令 cwd、环境和 ContextManager分别指向各自 Worktree；主目录和对方绝对路径访问被拒绝。（验证：真实仓库两成员工具场景）
- [ ] **AC9 同名修改隔离：**两个成员同时修改同名文件，结果只出现在各自 Worktree，主目录文件、分支和 cwd 保持不变。（验证：并发真实 Git 端到端）
- [ ] **AC9 shared 风险可见：**显式 shared 成员的状态、Prompt 和结果均标记 shared，不能显示成已 Worktree 隔离；并发写冲突风险对 Lead 可见。（验证：shared 状态/输出快照）
- [ ] **AC10 stop 有界：**running/starting stop 请求取消并在有界时间返回，idle 直接 stopped；重复 stop 幂等，超时保持 stopping且不强删。（验证：状态矩阵与可控慢 Runner）
- [ ] **AC10 delete 门禁：**存在 starting/running/stopping 成员时 delete 整体拒绝且不隐式 stop；无运行成员时才清空内存 Team 状态。（验证：各状态组合前后快照）
- [ ] **AC10 无成果清理：**stop/delete/exit 时，只有 dirty=false、HEAD=base 且检查可信的自动 Worktree被普通删除并安全删临时分支。（验证：三条生命周期各跑一个真实 clean 仓库）
- [ ] **AC10 成果保留：**dirty、新 commit、Git 检查失败或删除失败时，stop/delete/exit 后目录和分支仍可打开 review。（验证：四种真实/故障状态的 Git 对账）
- [ ] **AC10 完整报告：**成员结果与状态显示 path、branch、base、HEAD、dirty、kept、reason；长正文不会截掉 report 尾部，敏感值被替换。（验证：超长结果与秘密样本）
- [ ] **AC10 退出收敛：**应用退出并发取消最多三个成员，共享一个关闭窗口；结束后没有运行 Agent、Git 子进程或悬挂审批，重复关闭不重复 finalizer。（验证：三成员+审批的退出集成测试及进程检查）
- [ ] **AC11 不自动收敛 Git：**Team 运行、idle、stop、delete 和退出都不自动 commit、merge、cherry-pick、rebase 或解决冲突；主分支 HEAD 不变。（验证：记录 Git 调用参数并比较主分支前后）
- [ ] **AC11 人工收敛仍受保护：**用户未批准或主目录 dirty 时不执行人工合并；批准且干净时 Lead 只能通过现有 run_command，并继续受到审批、黑名单与 Hook 约束。（验证：三种条件下的命令执行决策）

## 状态、兼容与端到端

- [ ] **AC12 Team 状态完整：**工具与 `/team status` 可观察 Team 名称、目标、成员状态/isolation/Token/Worktree、当前任务、收件数及任务状态统计。（验证：混合 running/idle/stopped/failed 成员状态快照）
- [ ] **AC12 输出保护：**Team 工具、消息、状态和 Worktree报告统一脱敏并限制为 32 KiB，结构化 `truncated`/warning 与 review 尾部一致。（验证：秘密、超长 ASCII/中文输入）
- [ ] **AC12 关闭时旧行为不变：**Team disabled 或无活跃 Team 时，普通 `/status`、会话存档、长期记忆、Skills、Hooks、MCP 和主 Agent cwd 行为不变。（验证：启用前后旧功能回归快照）
- [ ] **AC12 文档和版本一致：**README、配置示例、帮助、运行时版本、package metadata 和 wheel 均描述同一套 0.8.0 Team 行为。（验证：构建后自动/人工对照）
- [ ] **AC13 定向测试：**全部 Team 测试及受影响的 SubAgent、Worktree、Orchestration、Permission、Hook、Command、Config、CLI、App 测试通过，无未解释失败或 skip。（验证：运行定向测试组并记录计数）
- [ ] **AC13 全仓门禁：**全仓 pytest、Ruff lint、格式检查、wheel 内容检查与 `git diff --check` 均以退出码 0 完成。（验证：保存每条命令和结果）

## 端到端场景

- [ ] **E2E 1 默认安全：**使用默认配置启动 → Team 工具可发现但返回 disabled → 项目配置尝试开启只产生 warning → 没有额外 Provider/Worktree/Token 活动。（验证：真实 App 启动与调用日志）
- [ ] **E2E 2 两成员协作：**用户级开启 Team → create → spawn 两个默认成员 → 建立有依赖任务 → 成员互发消息 → 上游完成解除下游阻塞 → idle 成员被单次续派 → Lead 下一次请求收到结果。（验证：可预测 Provider+真实临时 Git 仓库完整流程）
- [ ] **E2E 3 隔离成果保护：**两个成员修改同名文件 → 主目录保持不变 → stop 后有成果 Worktree保留 → delete 清空 Team 但不删除成果 → 可从报告路径打开两份内容。（验证：真实 Git 文件与分支检查）
- [ ] **E2E 4 退出与重启：**成员运行/等待审批时退出 → 无残留 Agent/Git/审批 → 有成果目录仍可 review → 重启后 Team 不恢复、旧成员工具失效、主仓库仍干净。（验证：真实进程生命周期）

## AC 覆盖映射

| Spec 验收标准 | Checklist 覆盖 |
|---|---|
| AC1 | 配置、费用与稳定入口第 1–4 项；E2E 1 |
| AC2 | 配置、费用与稳定入口第 5–6 项；身份世代测试 |
| AC3 | 配置、费用与稳定入口第 7–10 项 |
| AC4 | 成员创建第 1–5 项；命令发现项 |
| AC5 | 成员运行与普通任务兼容第 6–10 项 |
| AC6 | 工具权限与不可伪造身份全部 |
| AC7 | 共享任务板全部 |
| AC8 | 消息、投递与续派全部 |
| AC9 | 工具权限中的审批项；Worktree 隔离第 1–3 项 |
| AC10 | stop/delete/成果保护与退出项；E2E 3–4 |
| AC11 | Git 收敛边界两项 |
| AC12 | 状态、输出、兼容、文档和版本项 |
| AC13 | 定向测试、全仓门禁与四个 E2E |

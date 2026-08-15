# Kcode Worktree 隔离 Checklist

> 状态：已批准。每一项都通过运行代码或观察真实行为验证，不以阅读实现代替验收。标注“0.8.0 集成”的项目在 Agent Team 完成后关闭，不阻塞 M1 接口稳定性验收。

## Worktree 核心行为

- [ ] **AC1 仓库识别：**从普通仓库根目录和其子目录启动都识别到同一顶层；从非 Git 或 bare 仓库启动时 Kcode 仍可使用，但 Worktree 操作显示明确的不可用原因。（验证：在三类临时目录启动并执行 `/worktree list`）
- [ ] **AC2 合法创建：**`/worktree create demo-1` 在 `<repo_parent>/.kcode-worktrees/<repo_name>/demo-1` 创建分支 `kcode-worktree/demo-1`，结果显示绝对路径、分支和完整基线 commit。（验证：真实临时仓库执行命令后用 Git 查询交叉核对）
- [ ] **AC2 名称安全：**空值、`.`、`..`、大小写、空白、斜杠、反斜杠、绝对路径、遍历形式和超过 64 字符的名称全部被拒绝，约定根外没有新目录。（验证：参数化命令测试并比较操作前后目录树和分支列表）
- [ ] **AC2 冲突保护：**名称、目标目录、目标分支或 Git Worktree 登记项任一已占用时创建失败，已有目录、分支和内容不变。（验证：逐一预置四种冲突并比较前后 HEAD 与文件哈希）
- [ ] **AC3 Git 调用安全：**所有 Git 调用无 Shell、无 stdin/凭据交互且有超时与输出上限；创建不使用 `-B`，删除不使用 `--force`，分支不使用 `-D`。（验证：替身进程参数测试和超时子进程测试）
- [ ] **AC3 机器输出：**Worktree 路径包含空格或中文时仍能完整列举；缺字段、重复字段、非法 SHA 或截断输出产生安全错误而不是部分成功。（验证：porcelain `-z` 样本和真实 Git 仓库测试）
- [ ] **AC4 命令发现：**`/help` 可看到 `/worktree`，`/help worktree` 展示四个子命令用法；缺参、多参和未知子命令只显示错误/用法，不改变仓库。（验证：Command Dispatcher 与 Textual Pilot 测试）
- [ ] **AC4 管理闭环：**在干净仓库依次执行 create → list → status → remove，可观察到创建项、干净状态、普通删除成功，主仓库 cwd 和当前分支始终不变。（验证：真实临时仓库端到端场景）
- [ ] **AC5 手动 dirty 提示：**主目录有 tracked 或 untracked 修改时手动创建仍成功，但明确提示副本只来自 HEAD，Worktree 中不出现这些未提交内容。（验证：分别制造两类修改后执行 create 并读取副本）
- [ ] **AC5 自动 dirty 拒绝：**相同 dirty 状态下自动 SubAgent 隔离在 `git worktree add` 前拒绝，且不留下新目录、分支或元数据记录；Git 状态检查失败也同样拒绝。（验证：集成测试和故障注入后比较前后状态）

## 状态、所有权与安全删除

- [ ] **AC9 完整 dirty 检测：**tracked 修改、untracked 文件和删除项都显示 dirty；新 commit 即使工作区干净也显示 HEAD 已偏离基线。（验证：四种真实仓库状态逐项执行 `/worktree status`）
- [ ] **AC9 状态未知：**HEAD、status、元数据或 Git 列表任一无法可信读取时，结果显示 unknown/warning 且不可删除，不猜测为干净。（验证：损坏元数据与 Git 故障注入）
- [ ] **AC10 手动删除保护：**仅干净且 HEAD 等于基线的托管手动 Worktree 可删除；dirty、新 commit、unmanaged、missing 或检查失败时目录和分支保留。（验证：状态矩阵逐项执行 `/worktree remove`）
- [ ] **AC10 分支语义：**手动 remove 保留对应分支；自动无成果目录普通删除成功后才尝试 `branch -d`，失败只显示 warning，不升级为强制删除。（验证：分别执行手动与自动清理并观察分支）
- [ ] **AC10 自动成果保留：**隔离任务有未提交修改、新 commit、Git 检查失败或删除失败时，Worktree 和分支均可在任务结束后 review。（验证：完成、失败和取消路径分别制造成果并使用 Git 打开检查）
- [ ] **AC10 无成果自动清理：**隔离任务无修改、HEAD 等于基线且检查全部成功时，任务结束后 Worktree 被普通删除，临时分支被安全删除。（验证：前台、后台和取消的无成果场景）
- [ ] **AC11 所有权：**一个任务不能结束或删除另一个任务的 Worktree；手动 Worktree不参与任务自动清理。（验证：交换 owner 标识和混合手动/自动记录进行操作）
- [ ] **AC11 并发一致性：**多个同时启动的隔离任务得到不同名称、路径和分支，元数据不丢记录；冲突失败不破坏其它 Worktree。（验证：并发集成测试后与 `git worktree list` 对账）
- [ ] **元数据不污染仓库：**创建、列举和删除 Worktree 不在主仓库产生 `.kcode` 未跟踪文件；元数据位于相邻管理目录。（验证：操作前后运行 `git status --porcelain` 并检查管理目录）
- [ ] **元数据 fail closed：**损坏、未知版本、仓库路径错配、路径越界或符号链接逃逸时，不覆盖原元数据、不接管可疑目录、不执行自动删除。（验证：五类恶意/损坏样本并比较文件内容和目录存在性）

## SubAgent 隔离集成

- [ ] **AC6 兼容字段：**旧 Agent 定义默认为 shared；`shared` 与 `worktree` 可加载；非法值只让该定义失效并产生不泄漏正文的 warning；项目定义变化仍触发原信任变化。（验证：Parser、Catalog 与 Trust 回归测试）
- [ ] **AC7 前台隔离：**Worktree 定义式前台 SubAgent 的文件读写、搜索、命令 cwd、Git branch 和环境工作目录都指向其副本，主目录同名文件不变。（验证：使用可预测测试 Provider 驱动完整工具链）
- [ ] **AC7 后台隔离：**显式后台、自动超时转后台和 Esc 转后台沿用启动时的同一 Worktree，不重建、不切回 shared。（验证：三条路径比较任务前后路径与分支标识）
- [ ] **AC7 Prompt 通知：**隔离成员看到父目录和自身目录、绝对路径映射及“编辑前重新读取”通知；shared Agent 不出现隔离通知。（验证：捕获两类 Agent 的首轮 System Prompt）
- [ ] **AC7 主 Agent 稳定：**隔离任务运行前后，主 Agent `/status` 的 cwd、ToolContext、当前分支和工具 schema 均不改变。（验证：运行中及结束后分别获取状态和 schema 快照）
- [ ] **AC8 沙箱边界：**隔离 Agent 可访问自己的 Worktree，但主目录、其他 Worktree 和约定根外绝对路径均被拒绝。（验证：对三类路径执行读写工具并观察权限结果）
- [ ] **AC8 权限不扩张：**父权限上限、Plan Mode、危险命令黑名单、项目规则、Hook 和审批在隔离模式下保持有效。（验证：相同操作分别在 shared/worktree 下运行权限回归用例）
- [ ] **AC8 后台审批：**隔离后台任务的写入/命令审批继续进入原 FIFO 队列，显示正确 task 来源，批准只作用于对应请求。（验证：两个后台任务并发申请权限）
- [ ] **隔离任务续派边界：**已完成并执行收敛的 Worktree 任务拒绝普通 `task_send_message`；shared 完成任务仍能续派并保留原 Conversation。（验证：分别向两类 retained task 发送消息）
- [ ] **Hook 集成：**声明 worktree 的 Hook AgentAction 走同一隔离和结束策略，后台审批与递归阻断不变。（验证：Hook 成功、失败和递归尝试场景）

## 任务报告与退出

- [ ] **AC9 报告字段：**隔离任务完成、失败或取消后，主 Agent 都能看到名称、绝对路径、分支、base、HEAD、dirty、head_changed、kept 和保留原因。（验证：捕获三类 ToolResult/后台通知）
- [ ] **AC9 报告保护：**报告与 warning 中的 API Key/敏感值被替换，最终任务结果不超过 32 KiB，截断后仍可判断 Worktree 是否保留。（验证：注入标记密钥和超长输出）
- [ ] **finalizer 恰好一次：**前台、三种后台、正常完成、失败、取消和启动失败路径都只执行一次结束检查；finalizer 自身失败不覆盖原任务状态。（验证：带调用计数器的状态矩阵测试）
- [ ] **AC11 应用退出：**退出先取消运行中的 SubAgent，再执行有界安全检查；有成果项保留可 review，无成果项按规则清理，且无悬挂 Git 子进程、Agent task 或审批。（验证：运行中触发 App 退出并检查进程、任务、目录和分支）
- [ ] **关闭超时保守处理：**退出窗口内无法完成 Git 检查时保留元数据和现场，不因超时强制删除。（验证：注入慢 Git 客户端并在退出后检查记录）

## 兼容性与自动化门禁

- [ ] **AC12 用户文档：**README 说明 Git 前置条件、目录位置、四个命令、dirty 限制、成果保留与 review；明确没有 enter/exit、自动提交、自动合并或强制删除。（验证：按 README 在临时仓库手动执行示例）
- [ ] **AC12 旧数据兼容：**0.7.0 用户/项目配置、未含 isolation 的 Agent 定义和既有 session 无需迁移即可加载；非 Git 项目普通 SubAgent 仍可运行。（验证：加载旧 fixture 并运行 shared SubAgent）
- [ ] **稳定 Schema：**未启用 Worktree 的普通请求，基础工具和 Agent/Task 工具名称、参数 schema、注册顺序与基线一致。（验证：与 0.7.0 schema 快照对比）
- [x] **Worktree 定向测试：**全部 `tests/test_worktree_*.py` 通过。（证据：42 passed）
- [x] **受影响模块回归：**SubAgent、Hook、Permission、Command、CLI、App、Skill、MCP 和 History 定向测试通过。（证据：0.8.0 组合回归 229 passed；全仓 496 passed）
- [x] **全仓测试：**全仓 `pytest` 通过，无新增非预期 skip 或 warning。（证据：496 passed, 2 个既有 sandbox MCP skip）
- [x] **静态质量：**lint、格式和补丁空白检查全部通过。（证据：Ruff check、209 files formatted、`git diff --check` 均通过）
- [x] **构建资源：**wheel 构建成功，包含 `kcode.worktrees`，安装后版本与模块导入可用。（证据：隔离安装 `kcode-ai==0.8.0`，130 个 wheel 文件，Team/Worktree import smoke 通过）
- [x] **改动范围：**M1 diff 只包含 Worktree 文档、实现、测试和必要接缝，没有 Team、自动合并、环境复制或跨进程后端。（验证：审阅 `git diff --stat` 和 `git diff`）
- [x] **0.8.0 集成：**M2 Agent Team 实现后，`src/kcode/__init__.py`、`pyproject.toml`、README、配置示例及帮助中的版本/命令信息一致更新为 0.8.0。（证据：CLI `0.8.0`、wheel metadata `0.8.0`、16 条命令测试通过）

## 端到端场景

- [ ] **E2E 1——手动安全闭环：**在干净临时仓库执行 `/worktree create review-one` → `/worktree list` → `/worktree status review-one` → `/worktree remove review-one`，依次看到正确路径/分支/基线、干净可删除状态和安全清理，主目录始终不变。
- [ ] **E2E 2——手动成果保护：**创建手动 Worktree 后分别产生未提交修改和新 commit，remove 均拒绝；用户仍能进入报告路径检查成果。
- [ ] **E2E 3——并行隔离：**两个定义式 SubAgent 同时修改同名文件，结果位于两个不同 Worktree；主目录文件内容和分支不变，两份报告分别指向各自成果。
- [ ] **E2E 4——无成果自动清理：**前台或后台隔离 Agent只读完成后，通知生成前完成安全检查，Worktree 与临时分支不再存在。
- [ ] **E2E 5——失败仍保成果：**隔离 Agent 写入文件后失败、取消或应用退出，三种情况下目录和分支保留，主 Agent 能根据报告中的绝对路径与分支 review。
- [ ] **E2E 6——非 Git 降级：**从普通非 Git 项目启动，shared SubAgent 正常运行；手动命令和 worktree 隔离请求只返回明确错误，不导致 App 退出。

> 涉及真实模型调用的人工 E2E 可能产生 API 费用。自动化验收优先使用可预测的测试 Provider；如仍需真实模型调用，执行前必须向用户说明范围和费用风险。

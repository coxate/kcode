# Kcode Worktree 隔离 Tasks

> 状态：已批准。基于已批准的 `spec.md` 与 `plan.md`。任务必须按依赖顺序完成；每项完成后运行列出的验证，不用后续大测试代替当前小验证。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `src/kcode/worktrees/__init__.py` | 导出稳定公共类型与 Manager |
| 新建 | `src/kcode/worktrees/models.py` | slug 校验、记录、状态、报告与领域异常 |
| 新建 | `src/kcode/worktrees/git.py` | 有界 Git 子进程、仓库发现、porcelain 解析与 Git 操作 |
| 新建 | `src/kcode/worktrees/store.py` | 仓库外版本化元数据、严格校验与原子保存 |
| 新建 | `src/kcode/worktrees/manager.py` | 创建、列举、状态、安全删除与结束收敛 |
| 修改 | `src/kcode/subagents/models.py` | `AgentMeta.isolation` 与任务结束扩展模型 |
| 修改 | `src/kcode/subagents/factory.py` | 为定义式子 Agent 注入独立 Context 和隔离提示 |
| 修改 | `src/kcode/subagents/manager.py` | 通用 finalizer、恰好一次执行及关闭等待 |
| 修改 | `src/kcode/subagents/service.py` | Worktree 创建、启动失败收敛和 Hook 共用路径 |
| 修改 | `src/kcode/commands/models.py` | Worktree 命令所需 Host 协议 |
| 修改 | `src/kcode/commands/builtins.py` | `/worktree` 解析、注册与帮助信息 |
| 修改 | `src/kcode/cli.py` | 构造并注入单个 WorktreeManager |
| 修改 | `src/kcode/ui/app.py` | 命令委托、Manager 注入与 SubAgent 绑定 |
| 修改 | `src/kcode/__init__.py`、`pyproject.toml` | 在 0.8.0 版本级集成阶段统一更新版本号 |
| 修改 | `README.md` | Git 前置条件、命令、隔离与成果 review 说明 |
| 新建 | `tests/test_worktree_models.py` | slug、状态与报告单元测试 |
| 新建 | `tests/test_worktree_git.py` | Git runner、porcelain 与真实临时仓库测试 |
| 新建 | `tests/test_worktree_store.py` | 元数据边界、损坏与原子更新测试 |
| 新建 | `tests/test_worktree_manager.py` | 生命周期、并发、回滚和 fail-closed 测试 |
| 新建 | `tests/test_worktree_commands.py` | Slash Command 与 Host 委托测试 |
| 新建 | `tests/test_worktree_subagents.py` | 前后台隔离、权限、清理和报告集成测试 |
| 修改 | 既有 SubAgent、App、CLI、Command 与打包测试 | 兼容性和接线回归 |

## 第一阶段：建立安全内核

### T1：建立领域模型和异常边界

- 在 `models.py` 定义 WorktreeKind、Record、Status、FinalizationReport 及公开异常。
- 报告字段固定包含 path、branch、base、HEAD、dirty、head_changed、kept、reason、warnings。
- 验证：模型可构造、不可变，未知 Git 状态不能得到 `removable=true`。

### T2：实现并测试 slug 校验

- 实现长度不超过 64 的单段小写 slug 规则。
- 覆盖空值、点段、大小写、空白、两种斜杠、绝对路径和路径遍历。
- 验证：`uv run pytest tests/test_worktree_models.py -q`。

### T3：实现受限 Git 命令执行器

- 使用 `asyncio.create_subprocess_exec`，关闭 stdin 和凭据交互，不启用 Shell。
- 加入 30 秒默认超时、单流 64 KiB 上限及 terminate/kill/wait 回收。
- 验证：参数原样传递、环境变量、超时和超量输出测试通过，无残留进程。

### T4：实现 porcelain `-z` 解析器

- 解析 Worktree 路径、HEAD、branch、detached/bare/prunable 状态。
- 缺字段、重复关键字段、非法 SHA 或截断输入整批失败，不返回半份结果。
- 验证：空格、中文路径、多个 Worktree 与损坏输入测试通过。

### T5：实现 Git 仓库发现

- 通过 `git rev-parse` 确认顶层、inside-work-tree 和非 bare 状态。
- 将非 Git、bare、超时和非法输出转换为不阻止 Kcode 启动的 unavailable 原因。
- 验证：真实普通仓库、子目录、非 Git 目录和 bare 仓库测试通过。

### T6：实现 Git 状态与分支查询

- 实现 HEAD、dirty、branch_exists、worktree list 和单 Worktree 状态查询。
- dirty 使用 `--porcelain=v1 -z --untracked-files=all`，覆盖新增、修改、删除和 untracked。
- 验证：四种修改状态及 Git 查询失败测试通过。

### T7：实现 Git 创建和普通删除操作

- add 固定使用 `git worktree add -b <branch> <path> <base>`，不得出现 `-B`。
- remove 不带 `--force`；分支删除只使用 `git branch -d`。
- 验证：命令参数测试和真实临时仓库创建/删除测试通过。

## 第二阶段：元数据与生命周期

### T8：实现 Store 格式和严格加载

- 在 `.kcode-worktrees/<repo_name>/.metadata.json` 保存版本、规范仓库根与记录。
- 严格校验版本、字段、slug、SHA、kind、owner、规范路径和根边界。
- 验证：合法 round-trip 与字段缺失、未知版本、仓库错配测试通过。

### T9：实现 Store 的安全路径证明

- 对元数据和每个 Worktree 路径做规范化并验证仍位于约定根内。
- 符号链接逃逸、同名仓库元数据复用和越界记录一律 fail closed。
- 验证：路径遍历与 symlink escape 测试通过，仓库内容和状态未改变。

### T10：实现 Store 原子更新

- 在同目录创建临时文件，flush、fsync 后 `os.replace`，权限尽力设为 `0600`。
- 损坏原文件不自动覆盖；写入失败保留可诊断 warning。
- 验证：替换失败、中断前旧数据仍可读、权限与损坏文件测试通过。

### T11：建立 Manager 可用性和目录规则

- Manager 启动时只发现仓库，不执行 Git 变更。
- 计算相邻 Worktree 根，并提供 available、reason、repo_root、worktree_root。
- 验证：普通/非 Git/bare 初始化均不抛出启动异常。

### T12：实现手动创建前置检查

- 串行校验 slug、目标目录、目标分支、Git 已登记路径和分支占用。
- 记录 base commit；主目录 dirty 时允许继续并生成“仅基于 HEAD” warning。
- 验证：所有冲突在 add 前失败，脏目录 warning 可观察且未复制未提交内容。

### T13：实现手动创建和事后验证

- add 后重新核对路径、branch 与 HEAD，再原子写 Store。
- 返回路径、分支、base 和 warning；不切换主进程 cwd。
- 验证：真实临时仓库创建成功，主目录 cwd/branch/文件保持不变。

### T14：实现创建失败的安全回滚

- 区分 add 前失败、add 后验证失败和 Store 保存失败。
- 仅尝试普通 remove 与 `branch -d`；回滚失败保留现场并返回 review 信息。
- 验证：逐个故障点注入测试，无 `--force`、递归删除或数据覆盖。

### T15：实现自动 Agent 创建

- 生成 `agent-<12 hex>` 唯一名并绑定 owner_id。
- add 前证明主目录完全干净；dirty 或检查失败立即拒绝且无半成品。
- 验证：tracked/untracked dirty、Git 失败、名称碰撞和成功创建测试通过。

### T16：实现 Git 与 Store 对账列表

- 以 Git 列表为事实来源、Store 为所有权证明，按规范绝对路径匹配。
- 展示 managed、unmanaged、missing 和 warning，不因损坏元数据隐藏 Git 项。
- 验证：Git-only、Store-only、错分支、损坏 Store 四种场景测试通过。

### T17：实现单项状态检查

- 读取 HEAD 和完整 dirty 状态，与可信 base 比较 head_changed。
- 任何失败返回 unknown 且不可删除。
- 验证：干净无 commit、dirty、仅新 commit、Git 失败状态矩阵通过。

### T18：实现手动安全删除

- 只接受 managed 且 kind=manual、无 dirty、HEAD 等于 base 的记录。
- 普通删除成功后移除 Store 记录，但保留手动分支。
- 验证：干净目录删除成功且分支仍存在；其余状态均拒绝并保留目录和分支。

### T19：实现自动结束收敛

- 严格核对 record 与 owner_id；无成果时安全清理，有成果或未知状态时保留。
- 所有路径返回完整 FinalizationReport，不让 Git 检查异常向外泄漏敏感详情。
- 验证：成功、失败、取消共用的状态矩阵测试通过。

### T20：验证 Manager 并发所有权

- 用同一异步锁覆盖“检查→Git 变更→Store 更新”。
- 并发创建必须得到唯一项；错误 owner 不能结束其他任务 Worktree。
- 验证：并发压力与 owner mismatch 测试通过，无元数据丢记录。

### T21：固定公共导出

- 在 `worktrees/__init__.py` 只导出 UI/SubAgent 需要的模型、Manager 和异常。
- 避免上层直接依赖私有 Git/Store 实现。
- 验证：公共 import 测试和 `uv run ruff check src/kcode/worktrees tests/test_worktree_*.py`。

## 第三阶段：接入 SubAgent 生命周期

### T22：扩展 AgentMeta 隔离字段

- 新增严格的 `shared | worktree` 字段，默认 shared。
- 保持旧定义、原始内容指纹和非法定义 warning 语义。
- 验证：Parser/Catalog/Trust 测试覆盖默认值、两个合法值及非法值。

### T23：让 Factory 接收独立 ToolContext

- 为 `defined` 增加可选 Context；shared 路径保持原对象和原行为。
- worktree 路径复制父 Context，仅替换 workspace_root，并清除不应跨任务共享的取消状态。
- 验证：Factory 单元测试证明父 Context 未变、子 Context 指向副本。

### T24：注入 Worktree 环境提示

- 在定义式角色提示后追加父目录、隔离目录、绝对路径映射和重新读取要求。
- 不修改工具名称、参数模型或注册顺序。
- 验证：Prompt 快照包含通知，shared Prompt 与现有工具 schema 不变。

### T25：扩展 TaskRecord finalizer 数据

- 定义 TaskFinalizer 和 TaskFinalization，并在 TaskRecord 保存可选回调与消费状态。
- `launch` 默认不传 finalizer，保持所有旧调用兼容。
- 验证：现有 TaskManager 测试不改调用即可通过，新模型测试通过。

### T26：在正常结束路径恰好执行一次 finalizer

- 完成、失败和 Agent 主动取消后执行回调，再把 suffix/warnings 统一脱敏和截断。
- finalizer 异常只产生安全 warning，不覆盖原任务状态。
- 验证：三种结束状态、回调异常和重复完成测试的调用次数均为一。

### T27：覆盖脱离与后台通知

- 前台自动转后台、Esc 脱离和显式后台继续使用同一 TaskRecord/finalizer。
- 通知只在 finalizer 完成后生成，包含成果报告 warning。
- 验证：三种后台路径的通知顺序和报告测试通过。

### T28：收紧隔离任务续派

- finalizer 已消费的隔离任务拒绝普通 `task_send_message`。
- shared 完成任务仍保留原 Conversation 续派行为。
- 验证：isolated 拒绝、shared 成功和并发上限回归测试通过。

### T29：实现关闭时的有界收敛

- close 先请求 Runner 取消，再等待任务 finalizer；总窗口为 5 秒。
- 超时或检查失败保留 Store 记录，不执行猜测性删除；ApprovalBroker 仍被关闭。
- 验证：快速结束、慢 Git、finalizer 卡住和重复 close 测试通过。

### T30：让 Service 创建隔离定义式 Agent

- 对 worktree 定义先创建 Agent Worktree，再用其 Context 构造 ChildAgent并 launch。
- Fork、Skill Fork 和 shared 定义继续走原路径。
- 验证：前台/后台定义式隔离与三类 shared 回归测试通过。

### T31：收敛 Factory/launch 失败

- Worktree 创建成功后，Factory 或 TaskManager launch 失败都调用同一 finalize。
- 错误结果附安全报告；若无法证明无成果则保留现场。
- 验证：两个故障点注入测试均无无主 Worktree。

### T32：让 Hook AgentAction 复用隔离路径

- `launch_hook` 通过 Service 的同一创建辅助流程，不复制生命周期代码。
- 后台审批、递归阻断和父权限上限保持不变。
- 验证：Hook worktree 成功/失败及既有 Hook/SubAgent 回归测试通过。

## 第四阶段：命令与 App 接线

### T33：注册 `/worktree` 命令

- 注册一个 REQUIRED 参数命令，严格解析 create/list/status/remove 的参数个数。
- 未知子命令、缺 slug 和多余参数显示完整用法。
- 验证：CommandRegistry、Dispatcher 和 `/help worktree` 测试通过。

### T34：扩展 CommandHost 并实现 App 委托

- 增加四个异步 Host 方法，由 App 调用同一 Manager并输出中文可观察结果。
- `/status` 仍返回启动 cwd，不显示后台 Worktree 为当前目录。
- 验证：Fake Host 与 Textual Pilot 命令测试通过。

### T35：在 CLI/App 注入单一 Manager

- CLI 在确定规范 cwd 后构造 Manager，传给 App；App 再传给 Service。
- 测试或旧调用未传时由 App 安全构造默认实例，非 Git 项目仍能启动。
- 验证：CLI 接线、App 构造、非 Git 启动与退出测试通过。

### T36：验证手动命令端到端行为

- 在真实临时仓库依次执行 create/list/status/remove。
- 同时覆盖 dirty 警告、dirty 删除拒绝、新 commit 删除拒绝及 unmanaged 拒绝。
- 验证：`uv run pytest tests/test_worktree_commands.py -q`。

## 第五阶段：隔离与安全集成验收

### T37：验证基础工具统一使用隔离根

- 用定义式 Worktree SubAgent覆盖读、写、编辑、搜索和命令 cwd/Git branch。
- 证明主目录同名文件、主分支和主 Context 不变。
- 验证：相关 `tests/test_worktree_subagents.py` 场景通过。

### T38：验证权限沙箱与审批

- 子 Context 的 workspace_root 成为权限沙箱根。
- 访问主目录或其他 Worktree 的绝对路径被拒绝；后台写入仍进入原审批队列并携带 task 身份。
- 验证：隔离沙箱、Plan Mode、黑名单和前后台审批测试通过。

### T39：验证成果检测与报告保护

- 覆盖 tracked、untracked、删除、新 commit、Git 检查失败和无成果六类结局。
- 成功、失败、取消报告都经过敏感值脱敏和 32 KiB 总结果限制。
- 验证：报告字段、保留原因、脱敏和截断断言通过。

### T40：验证并行和退出行为

- 两个 SubAgent 同时修改同名文件，必须落在不同 Worktree；主目录不变。
- App 退出先取消任务；有成果的目录和分支可继续 review，无运行中 Git 子进程。
- 验证：并发隔离与 App 关闭集成测试通过。

## 第六阶段：文档和质量门禁

### T41：更新用户文档

- README 说明 Git 前置条件、相邻目录、四个命令、脏主目录限制、自动清理和成果 review。
- `/help` 能发现命令；明确没有 enter/exit、自动提交、自动合并和强制删除。
- 验证：文档命令与实际注册项一致，README 链接和示例可复制执行。

### T42：运行 Worktree 定向测试

- 运行全部 `tests/test_worktree_*.py`。
- 运行受影响的 SubAgent、Hook、Permission、Command、CLI 和 App 测试。
- 验证：所有定向测试通过，无 warning 被误当成成功状态。

### T43：运行 M1 全仓质量检查

- `uv run pytest`。
- `uv run ruff check .`。
- `uv run ruff format --check src tests`。
- 构建 wheel 并确认新包与 README 资源正确。
- `git diff --check`。
- 验证：命令全部以退出码 0 结束；失败时修复后从对应最小测试重新执行。

### T44：记录 M1 交付边界

- 按批准后的 Worktree checklist 完成行为验收并记录保留的 review 路径。
- 不开始 Agent Team 实现；先基于实际稳定接口生成并审批 Team 的四份文档。
- 版本号在 M2 完成后的 0.8.0 版本级集成任务统一从 0.7.0 更新，避免 M1 中途形成半成品发布状态。
- 验证：M1 无未解释失败，Team 和版本级任务都有明确后续依赖。

## 依赖顺序

```text
T1–T7 Git 与模型
  → T8–T21 Store/Manager
  → T22–T32 SubAgent 生命周期
  → T33–T36 命令与接线
  → T37–T40 安全集成
  → T41–T44 文档与门禁
```

不得并行实现具有写依赖的相邻阶段。测试编写可与对应实现任务同一小步完成，但每一步都必须先看到失败断言，再实现到通过。

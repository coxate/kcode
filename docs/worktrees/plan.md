# Kcode Worktree 隔离 Plan

> 状态：已批准。基于已批准的 `spec.md`。

## 架构概览

新增 `kcode.worktrees` 领域包，分成 Git 命令适配、元数据存储和生命周期管理三层。UI、SubAgent 与后续 Agent Team 只依赖 Manager，不自行拼装 Git 命令。

Worktree Manager 在 Kcode 启动时尝试发现 Git 仓库。发现失败不会阻止应用启动，Manager 保留明确的 unavailable 状态。所有手动命令仍会注册；调用时返回具体不可用原因。

SubAgent Service 在解析到 `isolation: worktree` 后，先由 Manager 创建唯一临时 Worktree，再让 Factory 使用该路径构造独立 `ToolContext` 和 `ContextManager`，最后把安全清理回调交给现有 `TaskManager`。因此前台、后台、自动转后台与取消路径共用同一生命周期，不新增第二套 Agent Loop。

## 核心数据结构与接口

### `WorktreeKind`

字符串枚举：

- `MANUAL = "manual"`：由 `/worktree create` 创建，不自动清理。
- `AGENT = "agent"`：由 SubAgent 创建，任务结束后执行安全清理。
- 后续 Team 可以新增 `TEAM = "team"`，不改变本里程碑接口。

### `WorktreeRecord`

不可变记录：

- `name: str`：已验证 slug。
- `path: Path`：规范化绝对路径。
- `branch: str`：`kcode-worktree/<slug>`。
- `base_commit: str`：创建前仓库 `HEAD` 的完整 SHA。
- `kind: WorktreeKind`。
- `owner_id: str | None`：自动 Worktree 的任务所有者；手动项为空。
- `created_at: float`：UTC Unix 时间。

### `WorktreeStatus`

不可变状态：

- `record: WorktreeRecord | None`：无可信元数据时为空。
- `path: Path`、`branch: str | None`、`head_commit: str | None`。
- `dirty: bool | None`：`None` 表示无法证明。
- `head_changed: bool | None`：与可信基线比较；无基线或 Git 失败时为 `None`。
- `managed: bool`：Git 列表和元数据清单一致且路径/分支均匹配。
- `removable: bool`：仅当 managed、`dirty is False`、`head_changed is False` 时为真。
- `warnings: tuple[str, ...]`。

### `WorktreeFinalizationReport`

不可变报告：

- Worktree 名称、路径、分支、base、HEAD。
- dirty、head_changed、kept、reason。
- warnings。
- `render()` 生成受 32 KiB 总任务结果限制约束的 `<worktree-result>` 文本；最终脱敏仍由 `TaskManager` 统一执行。

### `GitWorktreeClient`

异步 Git 适配接口：

- `discover(cwd) -> RepositoryInfo`
- `head(repo_root) -> str`
- `is_dirty(repo_root) -> bool`
- `branch_exists(repo_root, branch) -> bool`
- `list(repo_root) -> tuple[GitWorktreeEntry, ...]`
- `add(repo_root, path, branch, base_commit) -> None`
- `status(path) -> GitWorktreeState`
- `remove(repo_root, path) -> None`
- `delete_branch(repo_root, branch) -> None`

所有方法复用统一 `_run`：`asyncio.create_subprocess_exec`、参数数组、`stdin=DEVNULL`、stdout/stderr 管道、`GIT_TERMINAL_PROMPT=0`、空 `GIT_ASKPASS`、30 秒默认超时、64 KiB 单流上限。超时先 terminate，短等待后 kill，并完整 wait 回收进程。

`list` 解析 `git worktree list --porcelain -z`；字段缺失、重复关键字段、非法 SHA 或无终止字段时整次解析失败，不返回部分可信结果。

### `WorktreeStore`

元数据保存于仓库外侧的 `<repo_parent>/.kcode-worktrees/<repo_name>/.metadata.json`，格式为：

```json
{"version": 1, "records": [{"name": "...", "path": "...", "branch": "...", "base_commit": "...", "kind": "manual", "owner_id": null, "created_at": 0.0}]}
```

- 使用进程内锁配合临时文件、`fsync`、`os.replace` 原子更新，文件权限尽力收紧为 `0600`。
- 加载时严格校验版本、字段、slug、SHA、规范路径和 Worktree 根边界。
- 文件损坏时不覆盖原文件；Manager 产生 warning，并把 Git 发现项视为 unmanaged。
- Store 只保存身份与基线，不保存“当前进入的 Worktree”，因此不属于 session 恢复。
- 元数据不进入 Git 仓库，因此不会让主目录变脏，也不要求修改用户项目的 `.gitignore`。
- 记录包含规范化的仓库根目录；同名仓库发生目录复用或元数据错配时只降级为 unmanaged，不自动接管。

### `WorktreeManager`

构造参数：启动 cwd、Git client、Store。公开异步接口：

- `available`、`unavailable_reason`、`repo_root`、`worktree_root`。
- `create_manual(name) -> (WorktreeRecord, tuple[str, ...])`。
- `create_agent(owner_id) -> WorktreeRecord`。
- `list() -> tuple[WorktreeStatus, ...]`。
- `status(name) -> WorktreeStatus`。
- `remove_manual(name) -> WorktreeFinalizationReport`。
- `finalize(record, owner_id) -> WorktreeFinalizationReport`。

内部单一 `asyncio.Lock` 串行化检查、Git 变更和 Store 更新。自动名称为 `agent-<12 位 hex>`，创建前仍对目录、分支和 Git 已登记 Worktree 做三重占用检查。

## 模块设计

### 仓库发现与路径证明

- `discover` 运行 `git rev-parse --show-toplevel`、`--is-inside-work-tree` 和 `--is-bare-repository`，要求顶层目录存在且为非 bare 工作树。
- Worktree 根固定为 `repo_root.parent / ".kcode-worktrees" / repo_root.name`。
- 每次使用 Store 路径时重新 `resolve(strict=False)`，要求父目录解析后仍在 Worktree 根内；符号链接逃逸视为 unmanaged。
- 仓库目录同名碰撞由绝对 `repo_root` 校验补强：Store 顶层同时保存规范仓库路径；不匹配时拒绝加载。这样两个父目录中同名仓库不会互相认领记录。

### 创建流程

1. 校验可用性和 slug；自动创建先检查主目录 `git status --porcelain=v1 -z --untracked-files=all`，非空即拒绝。
2. 记录 `base_commit = HEAD`；检查目标目录不存在、分支不存在、Git Worktree 列表中路径与分支均未占用。
3. 创建 Worktree 根父目录；执行 `git worktree add -b <branch> <path> <base_commit>`。
4. 重新读取 Git Worktree 列表与 Worktree `HEAD`，验证路径、分支和 base 完全一致。
5. 原子写入 Store 后返回记录。
6. 若第 3～5 步失败，只能通过普通 `git worktree remove` 和安全 `git branch -d` 回滚本次新建项；任何回滚失败都保留现场并返回路径/分支 warning，绝不递归删除目录。

手动创建在第 1 步记录主目录 dirty 状态；dirty 时继续，但返回“只包含 HEAD”的 warning。自动创建在任何 Git 状态检查失败时 fail closed，且必须发生在 `git worktree add` 之前。

### 状态与删除流程

- `list` 以 Git porcelain 输出为事实来源，以 Store 为可信所有权来源；两者按规范绝对路径匹配。
- Store 有记录但 Git 没有对应项：标记 missing，不自动删除记录。
- Git 有 Kcode 路径但 Store 缺失/损坏：显示 unmanaged，不允许 status 的可删除判定或 remove。
- `status` 使用 `git status --porcelain=v1 -z --untracked-files=all` 和 `git rev-parse HEAD`；任何失败使 dirty/head_changed 为未知且 removable=false。
- 手动 `remove` 仅接受 kind=manual 且 removable=true 的 managed 项；删除工作目录和 Store 记录，但保留对应分支。
- 自动 `finalize` 校验 owner_id 和记录完全一致；无成果时普通 remove，成功后从 Store 删除记录，再尝试 `branch -d`。有成果或任何未知状态时只报告保留。
- Git Worktree 删除成功但 Store 更新失败时，报告持久化 warning；下次启动该记录显示 missing，不会误删其它数据。

### SubAgent Factory 与 Service

- `AgentMeta` 增加 `isolation: Literal["shared", "worktree"] = "shared"`，由 Pydantic 的 `extra="forbid"` 和 Literal 完成严格校验。
- `SubAgentFactory.defined` 新增可选 `context` 和额外稳定角色说明参数；未传时复用父 Context，传入时：
  - 用 `dataclasses.replace(parent.context, workspace_root=worktree_path, cancel_event=None, use_shell=False)` 创建上下文；
  - `AgentRunner` 由该上下文自动创建独立 `ContextManager(worktree_path, ...)`；
  - 权限引擎和 LocalPermissionStore 继续共享，但沙箱评估使用子 Context；
  - Prompt 在角色正文之后追加 `<worktree-context>`，不修改工具 Schema。
- Fork 与 Skill Fork 在 M1 保持 shared；`isolation` 只属于有定义元数据的定义式 SubAgent。
- `SubAgentService` 在构造时接收可选 `WorktreeManager`：
  - shared 定义走原路径；
  - worktree 定义先调用 `create_agent`，再构造 ChildAgent；
  - Factory 或 TaskManager launch 失败时立即调用同一 `finalize`，安全保留/清理并把报告写入错误结果；
  - 成功 launch 时把 finalizer 交给 TaskManager。
- Hook agent action复用 `SubAgentService` 的同一辅助路径，因此定义若声明 worktree，后台 Hook Agent 也遵守相同隔离和审批。

### TaskManager 生命周期扩展

新增通用、可选的完成回调，不耦合 Worktree 类型：

```python
TaskFinalizer = Callable[[TaskRecord], Awaitable[TaskFinalization]]

@dataclass(frozen=True)
class TaskFinalization:
    suffix: str = ""
    warnings: tuple[str, ...] = ()
```

- `launch(..., finalizer=None)` 把回调存入 TaskRecord。
- `_run` 先确定 completed/failed/cancelled 和基础结果，再在 `finally` 中恰好调用一次 finalizer。
- finalizer suffix 追加到成功结果或错误描述后，再统一脱敏和截断；warnings 进入 LaunchResult、Task 详情及后台通知。
- finalizer 自身异常不得覆盖原任务状态；转换成不含异常详情的 warning，Worktree 因无法证明安全而保留。
- `send_message` 复用同一个 TaskRecord 时不重复复用已消费 finalizer；M1 的自动 Worktree SubAgent 完成后已终结，其普通 `task_send_message` 必须返回“不支持续派隔离任务”，避免在已清理或已保留的旧目录继续运行。
- `close` 先调用 Runner cancel 并等待 `_run` 完成 finalizer；若超过现有关闭窗口，再取消任务协程，但仍用 shield 等待 finalizer 的有界 Git 检查。关闭总等待设为 5 秒，超时则保留 Store 记录，不执行删除。

### Slash Command 与 TUI 接入

- 在现有参数化命令框架注册一个 `/worktree`，ArgumentPolicy 为 REQUIRED，handler 解析首个子命令和单个 slug；未知参数或多余参数显示用法。
- `CommandHost` 增加四个异步方法：create/list/status/remove。`KCodeApp` 直接委托 WorktreeManager并格式化中文用户结果。
- WorktreeManager 由 CLI 在确定 cwd 后构造并注入 App；不在 CLI 阶段执行 Git 变更。
- App 把同一 Manager 注入 SubAgentService。
- 命令输出不进入 Conversation；Hook 仍收到现有 Slash Command 生命周期事件。

## 模块交互

### 自动隔离 SubAgent

```text
agent tool
  → SubAgentService 解析 isolation
  → WorktreeManager.create_agent（先检查主目录干净）
  → SubAgentFactory.defined（独立 ToolContext + Prompt notice）
  → TaskManager.launch(finalizer)
  → AgentRunner 使用 Worktree 环境运行/审批/取消
  → TaskManager 恰好一次调用 finalizer
  → WorktreeManager 安全删除或保留
  → 报告进入 ToolResult / task notification
```

### 手动命令

```text
/worktree ...
  → CommandDispatcher
  → KCodeApp CommandHost 方法
  → WorktreeManager
  → GitWorktreeClient + WorktreeStore
  → 中文可观察结果
```

## 文件组织

```text
src/kcode/worktrees/
├── __init__.py      # 稳定导出
├── models.py        # Record、Status、Report、异常
├── git.py           # 有界 Git 子进程与 porcelain 解析
├── store.py         # 版本化原子元数据
└── manager.py       # 生命周期、并发和所有权

docs/worktrees/      # spec / plan / task / checklist
tests/               # worktree git/store/manager/command/subagent 集成测试
```

既有接入点只修改命令 Host/注册、SubAgent models/factory/service/manager、App/CLI、README 和版本文件；不修改六个基础工具实现。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 工作目录传递 | 为子 Runner 复制 `ToolContext` | 文件、命令、沙箱和环境已统一依赖它，避免 ContextVar 与全局 cwd |
| Worktree 位置 | 仓库相邻隐藏目录 | 防止现有递归搜索扫描出多份源码 |
| 元数据 | 仓库外 `.kcode-worktrees/<repo_name>/.metadata.json` 原子清单 | 重启后仍能证明基线和所有权，不污染主仓库；损坏时可 fail closed |
| Git 发现 | porcelain `-z` | Git 官方稳定机器格式，可处理特殊路径 |
| 创建分支 | `-b` + 预检查 | 不移动或覆盖已有分支 |
| 删除 | 手动仅普通 remove；自动临时项再用 `branch -d` | 手动分支留给用户，自动无成果分支才安全收敛；两者都不强制丢成果 |
| 脏主目录 | 自动拒绝、手动警告 | 防止 Agent 基于缺少未提交修改的旧快照工作 |
| 后台生命周期 | TaskManager 通用 finalizer | 所有完成/失败/取消路径恰好一次收敛，后续 Team 可复用 |
| 工具改造 | 不修改基础工具 | 当前 ToolContext 已满足显式根目录需求，减少回归面 |
| 自动续派 | 隔离任务不支持 `task_send_message` | 任务结束后 Worktree 可能已删除或等待 review，隐式续写不安全 |

## Spec 覆盖

- F1–F5：Git client、Manager 创建/状态/命令流程。
- F6–F8：AgentMeta、Factory Context、Service 和现有权限链。
- F9–F11：Store、Status、Task finalizer、所有权和关闭流程。
- F12：Command/TUI、README、版本与回归测试任务。
- 无未归属需求，无循环依赖：UI/SubAgent → Manager → Git/Store，底层不反向依赖上层。

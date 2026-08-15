# Kcode Worktree 隔离 Spec

> 状态：已批准。

## 背景

Kcode 0.7.0 已有前台与后台 SubAgent。每个子 Agent 拥有独立对话、运行状态、权限审批状态和 Token 统计，但文件工具与命令工具仍指向主 Agent 的同一工作目录。两个并行 Agent 写入同一文件时，可能互相覆盖或读取到中间状态。

Git 分支可以记录不同提交路线，但一个普通工作目录同一时刻只能检出一条路线。Git Worktree 能让同一仓库同时存在多个工作目录和分支，适合作为并行 Agent 的文件系统隔离边界。

本里程碑只建立 Worktree 生命周期和 SubAgent 隔离能力；Agent Team 将在该接口稳定后作为下一个里程碑实现。

## 目标

- 为同一 Git 仓库创建、发现、检查和安全删除独立 Worktree。
- 让定义式前台或后台 SubAgent 可选择在独立 Worktree 中运行。
- 让隔离 SubAgent 的文件、命令、权限沙箱和环境信息统一指向自己的工作目录。
- 任何自动清理操作都以“不丢失可能有价值的成果”为最高优先级。
- 保持旧 Agent 定义、普通 SubAgent、Skill Fork、Hook、Slash Command 和会话行为兼容。

## 功能需求

### F1：可用性与仓库边界

- Worktree 功能只在 Kcode 启动目录属于非 bare Git 工作树时可用。
- 系统必须通过 Git 命令确认仓库顶层目录，不依据目录中是否存在 `.git` 文件夹进行猜测。
- 非 Git 项目继续支持普通共享目录 SubAgent；请求 Worktree 隔离或 Worktree 命令时返回明确错误，不影响 Kcode 启动。

### F2：名称、目录与分支

- Worktree 名称是单段小写 slug：以字母或数字开头，后续仅允许小写字母、数字和连字符，总长度不超过 64。
- 拒绝空值、`.`、`..`、斜杠、反斜杠、空白、绝对路径和任何路径遍历形式。
- 默认目录为 `<仓库父目录>/.kcode-worktrees/<仓库目录名>/<slug>`。
- 分支名为 `kcode-worktree/<slug>`。
- 名称、目标目录或目标分支已被其他 Worktree/分支占用时拒绝创建，不重置、不覆盖、不复用。

### F3：Git 执行安全

- 所有 Git 操作直接传递参数，不经过 Shell。
- 所有 Git 子进程禁用终端凭据交互、关闭标准输入并设置有限超时。
- 创建使用新分支语义，不允许重置已有分支。
- Worktree 发现使用 Git 稳定的机器可读输出，并正确处理路径中的空格和非 ASCII 字符。
- Git 命令超时、输出损坏或状态无法证明时返回安全错误；不得猜测为“干净”或“可以删除”。

### F4：手动管理命令

提供以下 Slash Command：

- `/worktree create <slug>`：从当前 `HEAD` 创建手动 Worktree，返回目录、分支和基线 commit。
- `/worktree list`：列出由当前仓库识别到的 Kcode Worktree 及路径、分支、HEAD 和状态。
- `/worktree status <slug>`：返回基线、当前 HEAD、是否存在未提交修改、是否存在基线后的 commit，以及是否可安全删除。
- `/worktree remove <slug>`：只删除已证明没有未提交修改且没有基线后 commit 的 Worktree。

本里程碑不提供 `enter` 或 `exit`；主 Agent 始终使用 Kcode 启动目录。

### F5：主目录未提交修改

- 手动创建 Worktree 时允许主目录存在未提交修改，但结果必须明确提示：新 Worktree 只包含当前 `HEAD`，不包含这些修改。
- 自动为 SubAgent 创建隔离目录前，必须检查主目录是否有 tracked 或 untracked 修改。
- 主目录不干净或状态无法确认时，自动创建必须拒绝，并提示用户先提交、清理现场或使用共享隔离模式。

### F6：Agent 定义兼容扩展

- Agent 定义新增 `isolation` 字段，合法值为 `shared` 和 `worktree`，默认 `shared`。
- 未配置该字段的旧 Agent 定义行为不变。
- 非法值依照现有严格 Parser 行为使该定义失效并产生不泄漏正文的 warning，不静默回退。
- 项目 Agent 信任指纹必须覆盖新增字段的原始定义内容，内容变化后沿用现有重新信任规则。

### F7：隔离运行环境

- `isolation: worktree` 的定义式 SubAgent 在启动执行前获得唯一 Worktree。
- 前台、显式后台、自动转后台和 Esc 转后台路径均保留 Worktree 隔离；转后台不能重建或丢失原 Worktree。
- 子 Agent 的所有相对文件路径、搜索路径、命令 cwd、权限沙箱、环境中的工作目录与 Git 状态必须以该 Worktree 为根。
- 主 Agent 的工具上下文和工作目录不得被修改。
- 子 Agent 上下文必须包含隔离通知：指出父目录与自身目录，要求把父对话中的绝对路径映射到本地副本，并在编辑前重新读取本地文件。
- Worktree 生命周期不得改变工具 Schema 或稳定工具名称。

### F8：权限、审批与既有安全层

- Worktree 隔离不能放宽父子权限上限、Plan Mode、危险命令黑名单、项目权限规则、Hook 或后台审批。
- 隔离子 Agent 仍通过现有审批队列请求写入或命令权限，来源继续包含任务身份。
- Worktree 路径成为该子 Agent 的沙箱根；访问主目录或其它 Worktree 的绝对路径必须被拒绝。

### F9：成果检测与任务报告

- 每个自动 Worktree 记录创建时基线 commit。
- 任务结束、失败或取消后检查两类成果：未提交修改，以及基线后的新 commit。
- 状态检查必须同时考虑 tracked、untracked 和删除项。
- 报告至少包含 Worktree 名称、绝对路径、分支、基线 commit、当前 HEAD、dirty 状态、新 commit 状态、是否保留及原因。
- 报告必须经过现有敏感值脱敏和结果大小限制。

### F10：安全清理

- 自动 Worktree 在“没有未提交修改、当前 HEAD 等于基线、所有 Git 检查成功”时，使用普通 Git Worktree 删除命令清理目录。
- 目录删除成功后，只能使用安全分支删除语义删除临时分支；若分支删除失败，报告 warning，不使用强制删除。
- 发现修改、新 commit、检查失败、删除失败时保留 Worktree 和分支，并把 review 信息交给主 Agent。
- 手动 Worktree 不参与任务结束自动清理。
- 用户命令和 Team 后续清理都不得使用强制删除或强制分支删除。

### F11：并发与所有权

- 同一 Kcode 进程内的 Worktree 创建和删除必须串行化，避免名称、目录和 Git 管理数据竞态。
- 自动名称必须在当前仓库内唯一，且可从名称识别为 Kcode 临时 Agent Worktree。
- 每个自动 Worktree 只能由创建它的任务负责结束检查和清理；普通任务不能删除其他任务的 Worktree。
- 应用退出时先取消子 Agent，再执行同一套安全成果检查；不得因为退出而强制清理。

### F12：用户可见信息与兼容性

- `/help` 能发现 `/worktree` 及其子命令用法。
- `/status` 继续显示主 Agent 的启动工作目录，不因后台隔离任务变化。
- README 说明 Git 前置条件、目录位置、手动命令、自动隔离语义、脏主目录限制和成果保留方式。
- Kcode 版本更新为 0.8.0 时，旧配置、旧 Agent 定义和旧 session 无需迁移即可加载。

## 非功能需求

- **N1 安全默认值：**任何无法证明可删除的状态都视为必须保留。
- **N2 无全局 cwd：**实现不得通过改变进程级当前目录切换 Agent 工作区。
- **N3 有界执行：**Git 命令必须有超时和输出上限，Kcode 退出时无悬挂子进程。
- **N4 路径完整性：**最终 Worktree 路径必须规范化，并被证明位于约定的仓库相邻根目录中。
- **N5 缓存稳定：**新增隔离字段不能让未启用 Worktree 的普通请求改变现有稳定工具 Schema。
- **N6 可测试性：**Git 调用层可被单元测试替换；核心生命周期同时使用真实临时 Git 仓库做集成测试。
- **N7 向后兼容：**现有 SubAgent、Skill、Hook、MCP、权限、历史和 TUI 测试保持通过。

## 不做的事

- 主 Agent 进入或退出 Worktree。
- 自动提交、暂存、合并、cherry-pick、rebase 或解决冲突。
- 强制删除 Worktree 或分支。
- 复制 `.env`、本地配置或其它 ignored 文件。
- 软链接或复制 `.venv`、`node_modules` 等依赖目录。
- 解析 `.git` 内部文件进行快速恢复。
- Worktree session 跨 Kcode 启动恢复。
- 过期 Worktree 后台扫描和自动删除。
- tmux、iTerm2、跨进程邮箱、Agent Team 或 Coordinator Mode。
- 对非 Git 项目模拟 Worktree。

## 验收标准

- **AC1（F1）：**Git 仓库中能识别顶层目录；非 Git 与 bare 仓库中 Kcode 可启动，但 Worktree 操作返回明确不可用错误。
- **AC2（F2）：**合法 slug 创建在约定相邻目录并使用约定分支；路径遍历、非法字符、重复名称、已有目录或已有分支全部被拒绝且不覆盖数据。
- **AC3（F3）：**测试证明 Git 不经过 Shell、禁交互且会超时；porcelain `-z` 输出能解析空格与非 ASCII 路径，损坏输出 fail closed。
- **AC4（F4）：**四个 `/worktree` 子命令均可从帮助发现，并能创建、列举、检查和安全删除真实临时仓库中的 Worktree。
- **AC5（F5）：**脏主目录允许手动创建并显示 HEAD 警告；相同状态下自动隔离拒绝，且不会留下目录或分支半成品。
- **AC6（F6）：**`shared/worktree` 解析正确；旧定义默认为 shared；非法值只使自身失效并 warning；项目定义变化触发原有信任变化。
- **AC7（F7）：**隔离前台和后台 SubAgent 的读、写、搜索、命令、环境 Git 状态都指向 Worktree，主目录同名文件保持不变。
- **AC8（F8）：**隔离 Agent 无法访问主目录绝对路径，无法通过 Worktree 绕过父权限、Plan Mode、黑名单、Hook 或审批。
- **AC9（F9）：**成功、失败和取消报告都包含规定字段；tracked、untracked、删除和新 commit 均能导致成果保留；敏感值不出现在报告中。
- **AC10（F10）：**无成果自动 Worktree 被普通删除并安全删分支；有成果或 Git 失败时目录和分支均保留；手动 Worktree从不自动清理。
- **AC11（F11）：**并发启动得到唯一 Worktree；创建冲突无残留；关闭 Kcode 不强制删除有成果 Worktree，也不留下运行中的 Git 子进程。
- **AC12（F12）：**README、帮助和版本信息更新；旧配置、Agent 定义、session 及现有全仓测试继续通过。

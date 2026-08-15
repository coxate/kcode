# Kcode SubAgent Tasks

> 状态：已批准。任务必须按依赖顺序完成，每项都要运行列出的验证。

## 第一阶段：定义与配置

### T1：新增 SubAgent 配置

- 新增 `SubAgentConfig`，默认启用、后台启用、120 秒、4 个运行、20 个保留。
- 只合并用户级配置；项目配置产生 warning。
- 验证：配置默认值、边界、项目忽略及旧配置测试通过。

### T2：实现核心数据模型

- 定义 Agent 来源、元数据、定义、摘要、任务状态、任务结果和通知。
- 固定权限严格度及父子模式求交函数。
- 验证：模型序列化、权限收紧和非法状态测试通过。

### T3：实现 Markdown Parser

- 严格解析 YAML frontmatter 和正文。
- 验证名称、Provider 字段、权限模式、轮次和工具列表。
- 实现 32 KiB、UTF-8、普通文件、符号链接和路径边界检查。
- 验证：`tests/test_subagent_parser.py` 通过。

### T4：实现信任存储

- 计算项目 Agent 清单指纹。
- 实现 `KCODE_SUBAGENT_TRUST_PATH`、私有权限和原子替换。
- 验证：首次信任、变化、拒绝、损坏文件和权限失败测试通过。

### T5：实现四级 Catalog

- 扫描插件、内置、用户和项目来源，实现覆盖、30 项限制和项目正文缓存。
- MCP 后校验 Provider 和工具名。
- 项目 `bypassPermissions` 拒绝，用户级产生 warning。
- 验证：`tests/test_subagent_catalog.py` 通过。

### T6：加入三个内置角色与打包资源

- 新增 `general-purpose`、`explore`、`plan`，后两者只允许 Kcode 只读工具。
- 验证：内置解析测试和 wheel 成员检查通过。

## 第二阶段：公共运行接口

### T7：实现 ProviderPool

- 复用当前主 Provider，其它 Provider 按配置名懒加载并缓存。
- 不暴露调用时 Provider 覆盖。
- 验证：继承、命名路由、复用和未知 Provider 测试通过。

### T8：扩展工具与审批公共接口

- `ToolSpec` 支持工具自行管理超时。
- `ApprovalRequest` 新增可选 task 来源，`SystemReminderMessage` 支持 `task`。
- 验证：现有工具、Provider 序列化和历史测试通过。

### T9：扩展 AgentRunner

- 保存只读 `DelegationSnapshot`，支持 Fork 首轮精确请求种子和一次性任务通知源。
- HookContext 标记主 Agent 或 SubAgent 来源。
- 子 Token 不更新主 ContextManager。
- 验证：`tests/test_agent_loop.py` 的快照、通知和上下文测试通过。

### T10：实现 Registry 过滤

- 定义式隐藏全部 SubAgent 控制工具，应用白名单、黑名单、后台基础集和显式 MCP。
- Fork 使用同 schema 拒绝代理，Skill Fork 隐藏控制工具。
- 验证：`tests/test_subagent_filter.py` 通过。

## 第三阶段：子 Runner 与任务运行时

### T11：实现 SubAgentFactory

- 构造定义式、Agent Fork 和 Skill Fork Runner。
- 创建独立 Conversation、ContextManager、Session、SkillRuntime 和 HookSession。
- 复用 ProviderPool、权限规则、LocalPermissionStore 和 HookEngine，应用父权限上限及角色最大轮次。
- 验证：`tests/test_subagent_factory.py` 通过。

### T12：实现 ApprovalBroker

- 前台审批直接调现有入口，后台审批进 FIFO 队列并暂停对应任务。
- 支持批准、拒绝、任务取消和应用关闭，不共享单次批准。
- 验证：`tests/test_subagent_approval.py` 通过。

### T13：实现 TaskManager

- 实现运行、脱离、停止、续派、关闭和状态转换。
- 实现 4 个运行、20 个保留及安全淘汰。
- 汇总 Token，脱敏并限制结果为 32 KiB，生成不持久化的一次性通知。
- 验证：`tests/test_subagent_manager.py` 通过。

### T14：实现三种后台路径

- 显式后台立即返回 task ID；前台到时或 Esc 由 Manager 接管但不取消 Runner。
- Ctrl+C 取消尚未脱离的父子任务。
- 验证：使用短测试超时覆盖显式、自动、Esc、Ctrl+C 和竞态。

## 第四阶段：工具、Hook 与 Skill

### T15：实现稳定 Agent/Task 工具

- 注册 `agent` 及四个 `task_*` 工具。
- 实现定义式同步结果、Fork 后台结果、task ID 操作和结构化错误。
- 工具 schema 不依赖 Catalog 内容。
- 验证：`tests/test_subagent_tools.py` 通过。

### T16：实现 Hook AgentAction

- 扩展 Hook Pydantic union 和 Parser 约束。
- 只允许异步定义式后台任务，支持模板、脱敏和任务满额 warning。
- 阻止 SubAgent 生命周期 Hook 再创建 Agent。
- 验证：Hook parser、engine 和递归阻断测试通过。

### T17：统一 Skill Fork 底座

- `SkillExecutor` 使用 `SubAgentFactory` 构造子 Runner。
- 保留前台事件、取消、Token、主历史和长期记忆回流。
- 验证：原 Skill Fork 测试及控制工具隐藏测试通过。

## 第五阶段：TUI 与启动

### T18：实现 AgentTrustScreen

- 展示项目路径、定义名称和指纹变化。
- 批准后原子保存，拒绝后跳过项目 Agent，存储失败安全降级。
- 验证：Textual Pilot 信任测试通过。

### T19：重排 KCodeApp 启动

- 注册稳定控制工具，完成 Skill/Hook/Agent 信任，MCP 后构建最终 Agent Catalog。
- 更新 Available Agents Prompt，绑定 TaskManager 和 Hook launcher，全部完成后才启用输入。
- 验证：无 MCP、有 MCP、MCP 失败和信任拒绝启动测试通过。

### T20：实现 TUI 后台交互

- 条件化 Esc，状态栏显示运行及等待审批数量。
- 后台审批 Worker 在前台生成结束后运行，完成/失败/取消只显示简短 notice。
- 退出前关闭 TaskManager。
- 验证：`tests/test_subagent_ui.py` 和 App 回归测试通过。

## 第六阶段：完整验证

### T21：定向自动化测试

运行新增测试以及受影响的 Agent、Tool、Hook、Skill、Config、App、MCP 和 History 测试。

### T22：全仓质量检查

- `uv run pytest`
- `uv run ruff check .`
- 本次文件 `ruff format --check`
- `git diff --check`
- wheel 构建和资源检查

### T23：tmux 端到端验证

真实运行 `uv run kcode`，验证项目信任、定义式前台、三种后台、Fork/cache usage、后台审批、Task 工具、Hook agent、Skill `/review`、Ctrl+C、退出清理和 JSONL 隔离。
